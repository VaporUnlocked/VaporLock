using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using HIDMaestro;
using Microsoft.Win32;
using Microsoft.Win32.SafeHandles;

namespace VaporLockControllerHost;

// Exclusive-use compatibility policy, not isolation from uncooperative admin
// applications. Never adopt a shared mapping/key or sweep by vendor name/index.
internal sealed class ControllerRuntimeOwnership : IVaporLockPreparedControllerComponent, IDisposable
{
    private const string RegistryRoot = @"SOFTWARE\HIDMaestro";
    private const string RegistrySecurity = "O:BAG:SYD:P(A;CI;KA;;;SY)(A;CI;KA;;;BA)(A;CI;KR;;;LS)";
    private readonly WindowsControllerComponentPlatform platform;
    private readonly ControllerComponentRecord record;
    private readonly Guid session = Guid.NewGuid();
    private readonly CancellationTokenSource stop = new();
    private readonly Task monitor;
    private readonly object sync = new();
    private Exception? conflict;
    private bool reserved;
    internal ControllerRuntimeOwnership(WindowsControllerComponentPlatform owner, ControllerComponentRecord owned)
    {
        platform = owner; record = owned;
        if (record.Phase != "installed" || record.Packages.Count != 2
            || !platform.TrustPresent("Root", record) || !platform.TrustPresent("TrustedPublisher", record))
            throw new IOException("Install or repair the VaporLock-managed controller component before starting it.");
        foreach (var package in record.Packages) platform.VerifyPackage(package, record);
        platform.HoldPayload(record);
        platform.ReconcileOwnedRuntime(record, retireConfiguration: false);
        RefuseForeignNamespace(record);
        monitor = Task.Run(async () =>
        {
            try
            {
                while (!stop.IsCancellationRequested)
                {
                    await Task.Delay(1000, stop.Token);
                    lock (sync) RefuseForeignNamespace(record);
                }
            }
            catch (OperationCanceledException) when (stop.IsCancellationRequested) { }
            catch (Exception error) { conflict = error; stop.Cancel(); }
        });
    }
    public string PreparedDirectory => record.PayloadDirectory;
    internal CancellationToken ConflictToken => stop.Token;
    public bool IsInstalled => record.Phase == "installed" && record.Packages.Count == 2;
    public void AssertInputsHeld()
    {
        if (conflict is not null) throw new IOException("A conflicting controller owner was detected. VaporLock stopped its own activity.", conflict);
        if (stop.IsCancellationRequested) throw new IOException("The controller ownership lease is closed.");
    }
    public void AssertNoForeignConflict()
    { AssertInputsHeld(); lock (sync) RefuseForeignNamespace(record); }
    public void ReserveIndex(int index)
    {
        AssertNoForeignConflict();
        if (index != 0) throw new IOException("This bridge reserves one owned controller slot per session.");
        lock (sync)
        {
            if (reserved) return;
            var operation = Guid.NewGuid();
            AddIntent(operation, "reserve-slot", "Controller0:" + session.ToString("N"));
            using var root = OpenOrCreateOwnedRoot();
            using var key = CreateRegistry(root, "Controller0", out var disposition);
            if (disposition != 1)
                throw new IOException("The shared controller slot already exists; it was not opened or overwritten.");
            key.SetValue("VaporLockSession", session.ToString("N"), RegistryValueKind.String);
            AddComplete(operation, "reserve-slot", "Controller0:" + session.ToString("N"));
            reserved = true;
        }
    }
    public void RecordCreated(int index, string instanceId)
    {
        AssertInputsHeld();
        if (!reserved || index != 0 || !instanceId.StartsWith(@"SWD\HIDMAESTRO", StringComparison.OrdinalIgnoreCase))
            throw new IOException("An unexpected controller instance was returned.");
        lock (sync)
        {
            if (record.Devices.Any(value => value.InstanceId.Equals(instanceId, StringComparison.OrdinalIgnoreCase)))
                throw new IOException("An existing controller instance cannot be claimed again.");
            var intent = record.Events.SingleOrDefault(entry => entry.Kind == "create-device" && entry.State == "intent"
                && instanceId.EndsWith("\\" + entry.Subject, StringComparison.OrdinalIgnoreCase)
                && !record.Events.Any(done => done.Operation == entry.Operation && done.State == "complete"))
                ?? throw new IOException("The controller instance has no owned creation intent.");
            if (!record.Creations.Any(item => item.Session == session && item.Suffix == intent.Subject
                && item.InstanceId.Equals(instanceId, StringComparison.OrdinalIgnoreCase) && !item.RemovalConfirmed))
                throw new IOException("The returned controller differs from its journaled creation inputs.");
            var (inf, service) = DeviceBinding(instanceId);
            if (!record.Packages.Any(package => package.PublishedInf.Equals(inf, StringComparison.OrdinalIgnoreCase)))
                throw new IOException("The new controller did not bind to an owned signed package.");
            record.Devices.Add(new(instanceId, index, inf, service, session));
            AddComplete(intent.Operation, "create-device", intent.Subject);
        }
    }
    public string BeginDeviceCreation(int index)
    {
        AssertNoForeignConflict();
        lock (sync)
        {
            if (index != 0 || !reserved) throw new IOException("The controller slot is not reserved.");
            var suffix = Guid.NewGuid().ToString("N").ToUpperInvariant() + "_0000";
            AddIntent(Guid.NewGuid(), "create-device", suffix);
            return suffix;
        }
    }
    public void RecordCreationInputs(string suffix, string enumerator, string[] hardwareIds, string[] compatibleIds, Guid container, string description)
    {
        AssertInputsHeld();
        lock (sync)
        {
            if (!reserved || !record.Events.Any(item => item.Kind == "create-device" && item.State == "intent" && item.Subject == suffix)
                || record.Creations.Any(item => item.Suffix == suffix)
                || !System.Text.RegularExpressions.Regex.IsMatch(enumerator, @"\AHIDMAESTRO_VID_[0-9A-Fa-f]{4}_PID_[0-9A-Fa-f]{4}&IG_00\z")
                || container == Guid.Empty || hardwareIds.Length is < 1 or > 8 || compatibleIds.Length is < 1 or > 8
                || hardwareIds.Concat(compatibleIds).Any(item => item.Length is < 1 or > 256 || item.Contains('|') || item.Contains('\0'))
                || description.Length is < 1 or > 256 || description.Contains('\0'))
                throw new IOException("Unexpected controller creation inputs; the helper was not launched.");
            record.Creations.Add(new(suffix, enumerator, hardwareIds.ToArray(), compatibleIds.ToArray(), container, description, session));
            platform.Save(record); // before the helper can create persistent state
        }
    }
    public bool RemoveOwnedInstance(string instanceId)
    {
        lock (sync)
        {
            var creation = record.Creations.SingleOrDefault(item => item.Session == session
                && item.InstanceId.Equals(instanceId, StringComparison.OrdinalIgnoreCase))
                ?? throw new IOException("No owned creation authority exists for this controller.");
            ControllerRuntimeRecovery.RemoveCreation(platform, record, creation); return true;
        }
    }
    public void AssertOwnedInstance(string instanceId)
    {
        lock (sync)
        {
            var entry = record.Devices.SingleOrDefault(value => value.InstanceId.Equals(instanceId, StringComparison.OrdinalIgnoreCase)
                && value.Session == session && !value.RemovalConfirmed) ?? throw new IOException("A foreign controller cannot be changed.");
            VerifyBinding(entry, record);
        }
    }
    public void RecordRemoved(string instanceId)
    {
        lock (sync)
        {
            var entry = record.Devices.Single(value => value.InstanceId.Equals(instanceId, StringComparison.OrdinalIgnoreCase) && value.Session == session);
            if (IsPresent(instanceId)) throw new IOException("The owned controller is still present; removal is pending.");
            record.Devices[record.Devices.IndexOf(entry)] = entry with { RemovalConfirmed = true }; platform.Save(record);
        }
    }
    public void RemoveOwnedInstanceConfiguration(int index)
    {
        lock (sync)
        {
            if (index != 0 || !reserved || record.Devices.Any(value => value.Session == session && !value.RemovalConfirmed))
                throw new IOException("Controller configuration cannot be removed before owned devices finish removal.");
            platform.ReconcileOwnedRuntime(record, retireConfiguration: false);
            reserved = false;
        }
    }
    public IDisposable AttachOwnedHelper(Process process)
    {
        AssertInputsHeld();
        var expected = Path.Combine(PreparedDirectory, "hmswd.exe");
        if (!Path.GetFullPath(process.MainModule?.FileName ?? "").Equals(expected, StringComparison.OrdinalIgnoreCase))
            throw new IOException("The controller helper process does not match its prepared image.");
        return new ControllerChildJob(process);
    }
    internal void AssertRemoved()
    {
        lock (sync)
        {
            platform.ReconcileOwnedRuntime(record, retireConfiguration: false);
            if (record.Devices.Any(value => value.Session == session && !value.RemovalConfirmed)
                || record.Creations.Any(value => value.Session == session && !value.RemovalConfirmed))
                throw new IOException("A controller device remains pending removal; it was not reported stopped.");
        }
    }
    internal static void RetireOwnedConfiguration(WindowsControllerComponentPlatform platform, ControllerComponentRecord owned, bool retireRoot)
    {
        using (var root = Registry.LocalMachine.OpenSubKey(RegistryRoot, writable: true))
        {
            if (root is null) return;
            VerifyRootMarker(root, owned);
            using (var slot = root.OpenSubKey("Controller0"))
            {
                if (slot is not null) VerifyRegistrySecurity(slot);
                ControllerRecoveryRules.ValidateRetirement(owned, root.GetValue("VaporLockComponentId") as string,
                    root.GetValueNames(), root.GetSubKeyNames(), slot?.GetValue("VaporLockSession") as string,
                    slot?.GetValueNames() ?? Array.Empty<string>(), slot?.GetSubKeyNames() ?? Array.Empty<string>());
            }
            if (root.GetSubKeyNames().Contains("Controller0"))
            {
                var action = Guid.NewGuid(); JournalConfiguration(platform, owned, action, "retire-slot", "intent");
                root.DeleteSubKey("Controller0", throwOnMissingSubKey: true);
                JournalConfiguration(platform, owned, action, "retire-slot", "complete");
            }
            if (root.GetSubKeyNames().Length != 0 || root.GetValueNames().Any(name => name != "VaporLockComponentId"))
                throw new IOException("Controller configuration changed during cleanup; it was preserved.");
        }
        if (retireRoot)
        {
            var action = Guid.NewGuid(); JournalConfiguration(platform, owned, action, "retire-root", "intent");
            Registry.LocalMachine.DeleteSubKey(RegistryRoot, throwOnMissingSubKey: false);
            JournalConfiguration(platform, owned, action, "retire-root", "complete");
        }
    }
    private static void JournalConfiguration(WindowsControllerComponentPlatform platform, ControllerComponentRecord owned, Guid action, string kind, string state)
    {
        if (state == "intent" && owned.Events.Count > 1022) throw new IOException("Controller journal cannot reserve retirement completion.");
        owned.Events.Add(new(action, kind, owned.Component.ToString("N"), state)); platform.Save(owned);
    }
    internal static void RefuseForeignNamespace(ControllerComponentRecord? owned)
    {
        using (var root = Registry.LocalMachine.OpenSubKey(RegistryRoot))
        {
            if (root is not null)
            {
                if (owned is null) throw new IOException("An existing HIDMaestro configuration has no VaporLock ownership record.");
                VerifyRootMarker(root, owned);
                if (root.GetValueNames().Any(name => name != "VaporLockComponentId"))
                    throw new IOException("Shared controller configuration was detected; it was preserved.");
                foreach (var name in root.GetSubKeyNames())
                {
                    if (name != "Controller0") throw new IOException("A foreign controller slot was detected; it was preserved.");
                    using var key = root.OpenSubKey(name);
                    if (key is not null) VerifyRegistrySecurity(key);
                    var owner = key?.GetValue("VaporLockSession") as string;
                    if (owner is null || !owned.Events.Any(entry => entry.Kind == "reserve-slot" && entry.Subject == name + ":" + owner))
                        throw new IOException("Controller configuration ownership is ambiguous; it was preserved.");
                }
            }
        }
        foreach (var id in DriverInstances())
            if (owned is null || (!owned.Devices.Any(value => value.InstanceId.Equals(id, StringComparison.OrdinalIgnoreCase) && !value.RemovalConfirmed)
                && !owned.Events.Any(entry => entry.Kind == "create-device" && entry.State == "intent"
                    && id.EndsWith("\\" + entry.Subject, StringComparison.OrdinalIgnoreCase)
                    && !owned.Events.Any(done => done.Operation == entry.Operation && done.State == "complete"))))
                throw new IOException("An existing or newly conflicting HIDMaestro device is not VaporLock-managed.");
    }
    private static void VerifyBinding(ControllerDeviceEntry device, ControllerComponentRecord owned)
    {
        if (!IsPresent(device.InstanceId)) return;
        var (inf, service) = DeviceBinding(device.InstanceId);
        if (!inf.Equals(device.InfName, StringComparison.OrdinalIgnoreCase) || !service.Equals(device.Service, StringComparison.OrdinalIgnoreCase)
            || !owned.Packages.Any(package => package.PublishedInf.Equals(inf, StringComparison.OrdinalIgnoreCase)))
            throw new IOException("The recorded controller's driver binding changed; it was preserved.");
    }
    internal static (string Inf, string Service) DeviceBinding(string id, bool allowUnbound = false)
    {
        if (!id.StartsWith(@"SWD\HIDMAESTRO", StringComparison.OrdinalIgnoreCase) || id.Contains("..") || id.Length > 512)
            throw new IOException("Invalid owned controller instance.");
        using var node = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Enum\" + id)
            ?? throw new IOException("Windows has not registered the controller instance.");
        var rawDriver = node.GetValue("Driver");
        var rawService = node.GetValue("Service");
        if (rawService is not null and not string || rawDriver is not null and not string)
            throw new IOException("Unexpected controller driver-binding data.");
        var service = rawService as string ?? "";
        if (rawDriver is null && allowUnbound) return ("", service);
        var driver = rawDriver as string ?? throw new IOException("Windows has not bound the controller driver.");
        if (!System.Text.RegularExpressions.Regex.IsMatch(driver, @"\A\{[0-9A-Fa-f-]{36}\}\\[0-9A-Fa-f]{4}\z"))
            throw new IOException("Unexpected controller driver-class identity.");
        using var binding = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Control\Class\" + driver)
            ?? throw new IOException("Controller driver binding is unavailable.");
        return (binding.GetValue("InfPath") as string ?? "", service);
    }
    internal static IEnumerable<string> DriverInstances()
    {
        foreach (var enumerator in new[] { "SWD", "ROOT" })
        {
            using var root = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Enum\" + enumerator);
            if (root is null) continue;
            foreach (var family in root.GetSubKeyNames().Where(name => name.StartsWith("HIDMAESTRO", StringComparison.OrdinalIgnoreCase)))
            {
                using var group = root.OpenSubKey(family);
                if (group is null) continue;
                foreach (var instance in group.GetSubKeyNames())
                { var id = enumerator + "\\" + family + "\\" + instance; if (IsPresent(id)) yield return id; }
            }
        }
    }
    private RegistryKey OpenOrCreateOwnedRoot()
    {
        using var machine = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64);
        var key = CreateRegistry(machine, RegistryRoot, out var disposition);
        if (disposition == 1) key.SetValue("VaporLockComponentId", record.Component.ToString("N"), RegistryValueKind.String);
        else VerifyRootMarker(key, record);
        return key;
    }
    private static RegistryKey CreateRegistry(RegistryKey parent, string path, out uint disposition)
    {
        if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(RegistrySecurity, 1, out var descriptor, out _))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        var attributes = new SecurityAttributes { Length = Marshal.SizeOf<SecurityAttributes>(), Descriptor = descriptor };
        try
        {
            var error = RegCreateKeyExW(parent.Handle, path, 0, null, 0, 0xf003f, ref attributes, out var handle, out disposition);
            if (error != 0) throw new Win32Exception(error);
            return RegistryKey.FromHandle(handle, RegistryView.Registry64);
        }
        finally { LocalFree(descriptor); }
    }
    private static void VerifyRootMarker(RegistryKey root, ControllerComponentRecord owned)
    {
        VerifyRegistrySecurity(root);
        if (root.GetValue("VaporLockComponentId") as string != owned.Component.ToString("N")) throw new IOException("The shared controller root is not positively owned.");
    }
    private static void VerifyRegistrySecurity(RegistryKey key)
    {
        var error = GetSecurityInfo(key.Handle, 4, 5, out _, out _, out _, out _, out var descriptor);
        if (error != 0) throw new Win32Exception((int)error);
        try
        {
            var size = GetSecurityDescriptorLength(descriptor);
            if (size == 0 || size > 65536) throw new IOException("Controller registry security is unavailable.");
            var bytes = new byte[size]; Marshal.Copy(descriptor, bytes, 0, bytes.Length);
            var security = new RawSecurityDescriptor(bytes, 0);
            var admin = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
            var system = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
            var service = new SecurityIdentifier(WellKnownSidType.LocalServiceSid, null);
            if ((security.Owner != admin && security.Owner != system) || security.DiscretionaryAcl is not { Count: 3 } acl
                || (security.ControlFlags & ControlFlags.DiscretionaryAclProtected) == 0)
                throw new IOException("Controller configuration is not administrator protected.");
            var seen = new HashSet<string>();
            foreach (var raw in acl)
                if (raw is not CommonAce ace || ace.IsCallback || ace.AceQualifier != AceQualifier.AccessAllowed
                    || ace.AceFlags != AceFlags.ContainerInherit || !seen.Add(ace.SecurityIdentifier.Value)
                    || !(ace.SecurityIdentifier == service && ace.AccessMask == 0x20019
                        || (ace.SecurityIdentifier == admin || ace.SecurityIdentifier == system) && ace.AccessMask == 0xf003f))
                    throw new IOException("Unexpected controller registry access rule; existing state was preserved.");
        }
        finally { LocalFree(descriptor); }
    }
    private void AddIntent(Guid operation, string kind, string subject)
    {
        if (record.Events.Count > 1022) throw new IOException("Controller journal cannot reserve completion.");
        record.Events.Add(new(operation, kind, subject, "intent")); platform.Save(record);
    }
    private void AddComplete(Guid operation, string kind, string subject)
    { record.Events.Add(new(operation, kind, subject, "complete")); platform.Save(record); }
    private static bool IsPresent(string id) => ControllerStateQueries.Persistent(id);
    internal static SafeFileHandle AcquireOperationReservation()
    {
        if (!ConvertStringSecurityDescriptorToSecurityDescriptorW("O:BAG:SYD:P(A;;GA;;;SY)(A;;GA;;;BA)", 1, out var descriptor, out _))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        var attributes = new SecurityAttributes { Length = Marshal.SizeOf<SecurityAttributes>(), Descriptor = descriptor };
        SafeFileHandle handle; int error;
        try { handle = CreateMutexExW(ref attributes, @"Global\VaporLock.ControllerComponent.Exclusive", 0, 0x1f0001); error = Marshal.GetLastWin32Error(); }
        finally { LocalFree(descriptor); }
        if (handle.IsInvalid) { handle.Dispose(); throw new Win32Exception(error); }
        if (error == 183) { handle.Dispose(); throw new IOException("Another controller session owns the exclusive component reservation."); }
        return handle;
    }
    public void Dispose()
    {
        stop.Cancel(); try { monitor.GetAwaiter().GetResult(); } catch (OperationCanceledException) { }
        try { platform.ReconcileOwnedRuntime(record, retireConfiguration: false); }
        finally { stop.Dispose(); }
    }
    [StructLayout(LayoutKind.Sequential)] private struct SecurityAttributes { public int Length; public IntPtr Descriptor; public int Inherit; }
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode)] private static extern int RegCreateKeyExW(SafeRegistryHandle parent, string path, uint reserved, string? type, uint options, uint access, ref SecurityAttributes security, out SafeRegistryHandle key, out uint disposition);
    [DllImport("advapi32.dll")] private static extern uint GetSecurityInfo(SafeRegistryHandle handle, int type, uint parts, out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr descriptor);
    [DllImport("advapi32.dll")] private static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string sddl, uint revision, out IntPtr descriptor, out uint size);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern SafeFileHandle CreateMutexExW(ref SecurityAttributes security, string name, uint flags, uint access);
    [DllImport("kernel32.dll")] private static extern IntPtr LocalFree(IntPtr memory);
    [DllImport("cfgmgr32.dll", CharSet = CharSet.Unicode)] private static extern uint CM_Locate_DevNodeW(out uint node, string id, uint flags);
    [DllImport("cfgmgr32.dll", CharSet = CharSet.Unicode)] private static extern uint CM_Query_And_Remove_SubTreeW(uint node, out uint vetoType, StringBuilder veto, int size, uint flags);
}
