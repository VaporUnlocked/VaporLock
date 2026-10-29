using System;
using System.Collections.Generic;
using System.Linq;

namespace HIDMaestro;

// Our separately identified integration has no implicit driver-install authority.
// The host retains protected inputs/provenance for the full device lifetime.
public interface IVaporLockPreparedControllerComponent
{
    string PreparedDirectory { get; }
    bool IsInstalled { get; }
    void AssertInputsHeld();
    void ReserveIndex(int index);
    string BeginDeviceCreation(int index);
    void RecordCreationInputs(string suffix, string enumerator, string[] hardwareIds, string[] compatibleIds, Guid container, string description);
    bool RemoveOwnedInstance(string instanceId);
    void AssertNoForeignConflict();
    void RecordCreated(int index, string instanceId);
    void AssertOwnedInstance(string instanceId);
    void RecordRemoved(string instanceId);
    void RemoveOwnedInstanceConfiguration(int index);
    IDisposable AttachOwnedHelper(System.Diagnostics.Process process);
}

public static class VaporLockIntegration
{
    private static readonly object Sync = new();
    private static IVaporLockPreparedControllerComponent? component;
    private static readonly Dictionary<int, HashSet<string>> owned = new();

    // Pure embedded metadata access for the host's driver-free self-test.
    // Does not construct HMContext or prepare/install anything.
    public static HMProfile EmbeddedProfile(string id)
    {
        var profile = Internal.ProfileDatabase.LoadEmbedded().All.SingleOrDefault(value => value.Id == id)
            ?? throw new ArgumentException("The embedded controller profile is unavailable.");
        return new HMProfile(profile);
    }

    public static void Configure(IVaporLockPreparedControllerComponent prepared)
    {
        ArgumentNullException.ThrowIfNull(prepared);
        lock (Sync)
        {
            if (component is not null) throw new InvalidOperationException("Controller integration is already configured.");
            prepared.AssertInputsHeld();
            component = prepared;
        }
    }

    private static IVaporLockPreparedControllerComponent RequireComponent()
    {
        var result = component ?? throw new InvalidOperationException("Prepare the VaporLock controller component explicitly first.");
        result.AssertInputsHeld();
        return result;
    }

    internal static string? PreparedDirectory => component?.PreparedDirectory;
    internal static string RequirePrepared() => RequireComponent().PreparedDirectory;
    internal static bool IsReady() => component is not null && RequireComponent().IsInstalled;

    internal static void RequireReady(int index)
    {
        var current = RequireComponent();
        if (!current.IsInstalled) throw new InvalidOperationException("The owned controller driver packages are not ready.");
        current.AssertNoForeignConflict();
        current.ReserveIndex(index);
    }

    internal static string BeginDeviceCreation(int index) => RequireComponent().BeginDeviceCreation(index);
    internal static void RecordCreationInputs(string suffix, string enumerator, string[] hardwareIds, string[] compatibleIds, Guid container, string description)
        => RequireComponent().RecordCreationInputs(suffix, enumerator, hardwareIds, compatibleIds, container, description);
    internal static bool RemoveOwnedDeviceExact(string instanceId) => RemovalComponent().RemoveOwnedInstance(instanceId);

    public static async System.Threading.Tasks.Task<string> ReadBoundedHelperOutput(System.IO.StreamReader reader)
    {
        var result = new System.Text.StringBuilder(); var buffer = new char[4096]; bool overflow = false; int count;
        while ((count = await reader.ReadAsync(buffer.AsMemory())) != 0)
        {
            if (result.Length + count <= 65536) result.Append(buffer, 0, count);
            else overflow = true;
        }
        if (overflow) throw new InvalidOperationException("The prepared controller helper exceeded its output bound.");
        return result.ToString();
    }

    internal static void RecordCreated(int index, string instanceId)
    {
        if (string.IsNullOrWhiteSpace(instanceId)) throw new InvalidOperationException("Windows did not return a new controller instance.");
        var current = RequireComponent();
        current.RecordCreated(index, instanceId);
        lock (Sync)
        {
            if (!owned.TryGetValue(index, out var devices)) owned[index] = devices = new(StringComparer.OrdinalIgnoreCase);
            if (!devices.Add(instanceId)) throw new InvalidOperationException("The controller instance was already claimed.");
        }
    }

    internal static string[] OwnedForIndex(int index)
    {
        lock (Sync) return owned.TryGetValue(index, out var devices) ? devices.ToArray() : Array.Empty<string>();
    }

    internal static void RequireOwned(string instanceId)
    {
        lock (Sync)
            if (!owned.Values.Any(devices => devices.Contains(instanceId)))
                throw new InvalidOperationException("A foreign controller instance cannot be modified or removed.");
        RemovalComponent().AssertOwnedInstance(instanceId);
    }

    internal static void RecordRemoved(string instanceId)
    {
        RemovalComponent().RecordRemoved(instanceId);
        lock (Sync) foreach (var devices in owned.Values) devices.Remove(instanceId);
    }

    internal static void RemoveOwnedInstanceConfiguration(int index)
        => RemovalComponent().RemoveOwnedInstanceConfiguration(index);

    // A newly detected foreign conflict stops normal operations, but must not
    // prevent exact recorded-device teardown while host input leases remain.
    private static IVaporLockPreparedControllerComponent RemovalComponent()
        => component ?? throw new InvalidOperationException("Controller ownership is unavailable.");

    internal static IDisposable AttachOwnedHelper(System.Diagnostics.Process? process)
        => RequireComponent().AttachOwnedHelper(process ?? throw new InvalidOperationException("Windows did not start the prepared controller helper."));
}
