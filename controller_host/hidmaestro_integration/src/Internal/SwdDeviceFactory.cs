using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;

namespace HIDMaestro.Internal;

/// <summary>
/// Creates SWD-enumerated (software-device) PnP devices via the <c>hmswd.exe</c>
/// helper process — which wraps <c>cfgmgr32!SwDeviceCreate</c> in native C.
///
/// <para><b>Why an out-of-process helper:</b> on .NET 10 / Win11 26200,
/// direct P/Invoke to <c>SwDeviceCreate</c> returns
/// <c>hr = 0x8007007E (ERROR_MOD_NOT_FOUND)</c> synchronously, even though
/// the identical C call succeeds. CoInitialize, apartment changes, delegate-
/// vs-function-pointer marshaling, <c>UnmanagedCallersOnly</c>, and DLL
/// preloading were all tried and did NOT resolve it. Rather than block
/// the slot-1-skip fix on root-causing a .NET runtime marshaling bug,
/// the SDK shells out to a 200-line native helper that performs the same
/// call from C (where it works) and prints the resulting instance ID.</para>
///
/// <para><b>Why this migration matters:</b> xinput1_4.dll's slot allocator
/// reads <c>DEVPKEY_Device_ContainerId</c> on any XUSB-interface device it
/// enumerates. A null-sentinel container triggers a "this is an embedded/
/// primary device" branch that sets bit 2 on the allocator's device struct,
/// which (a) skips internal slot 0 during fallback allocation and (b) marks
/// the device for a primary-swap that makes slot 1 present as empty. Giving
/// our virtuals a real ContainerId via SwDeviceCreate's explicit
/// <c>pContainerId</c> bypasses both. See memory:
/// project-slot-1-skip-swd-migration-plan.md.</para>
///
/// <para><b>Device identity:</b> none of the properties consumers care about
/// are affected by the ROOT→SWD switch. HardwareIds, CompatibleIds, VID/PID,
/// HID descriptor, interface GUIDs all pass through unchanged — only the
/// InstanceId path prefix becomes <c>SWD\HIDMAESTRO\&lt;instance&gt;</c>.</para>
///
/// <para><b>Lifetime:</b> <c>hmswd.exe</c> sets
/// <c>SWDeviceLifetimeParentPresent</c> so devices persist past the helper
/// process's exit. Cleanup is via standard PnP DIF_REMOVE (which the
/// existing HIDMaestro teardown paths already do).</para>
/// </summary>
internal static class SwdDeviceFactory
{
    /// <summary>Default enumerator name. Callers can override per device
    /// kind. For Xbox Series BT gamepad companions we pass
    /// <c>"VID_xxxx&amp;PID_yyyy&amp;IG_00"</c> so the HID child PDO also
    /// inherits that name in its instance ID — matching the pattern that
    /// xinputhid's INF selects against.</summary>
    public const string DefaultEnumeratorName = "HIDMAESTRO";

    public readonly struct Result
    {
        public readonly bool    Success;
        public readonly string? InstanceId;       // e.g. "SWD\\HIDMAESTRO\\HMC_0001"
        public readonly int     HResult;

        public Result(bool success, string? instanceId, int hr)
        {
            Success = success; InstanceId = instanceId; HResult = hr;
        }
    }

    /// <summary>Build a per-controller deterministic ContainerId GUID.
    /// Shared between the main HID device and any companion devices for the
    /// same controller so PnP / Windows Settings group them as one physical
    /// controller (per "How Container IDs are Generated" docs: "all devnodes
    /// that belong to a container on a given bus type must share the same
    /// ContainerID value").</summary>
    /// <remarks>
    /// Encoding: <c>{48494430-4D41-4553-5452-4F000000&lt;idx:X4&gt;}</c> =
    /// ASCII "HIDMAESTRO" + 16-bit index. Non-sentinel, deterministic,
    /// per-controller unique up to 65535 controllers.
    /// </remarks>
    public static Guid ContainerIdFor(int controllerIndex)
    {
        byte[] d4 =
        {
            0x54, 0x52, 0x4F, 0x00,            // "TRO\0"
            0x00, 0x00,                         // reserved
            (byte)((controllerIndex >> 8) & 0xFF),
            (byte)(controllerIndex & 0xFF),
        };
        return new Guid(0x48494430, 0x4D41, 0x4553, d4);
    }

    /// <summary>Create an SWD-enumerated device with the given properties
    /// via <c>hmswd.exe</c>. Blocks until the helper returns (device fully
    /// enumerated) and returns its InstanceId.</summary>
    /// <param name="instanceIdSuffix">Appended after the enumerator —
    /// becomes the full instance-id's last segment. Must be unique
    /// across live devices under this enumerator.</param>
    /// <param name="hardwareIds">List of hardware IDs. INFs match by
    /// string on these.</param>
    /// <param name="compatibleIds">List of compatible IDs, or empty.</param>
    /// <param name="containerId">Non-sentinel container GUID. Use
    /// <see cref="ContainerIdFor"/> for the HIDMaestro convention.</param>
    /// <param name="deviceDescription">Human-readable description.</param>
    public static Result Create(
        string instanceIdSuffix,
        string[] hardwareIds,
        string[] compatibleIds,
        Guid containerId,
        string deviceDescription,
        bool driverRequired = true,
        int callbackTimeoutMs = 35000,
        string? enumeratorName = null)
    {
        string? helperPath = EnsureHelperExtracted();
        if (helperPath == null || !File.Exists(helperPath))
            return new Result(false, null, unchecked((int)0x80070002)); // ERROR_FILE_NOT_FOUND

        // hmswd expects '|' as the list separator.
        string hwList     = string.Join('|', hardwareIds ?? Array.Empty<string>());
        string compatList = string.Join('|', compatibleIds ?? Array.Empty<string>());
        string guidStr    = containerId.ToString("B");
        string enumName   = enumeratorName ?? DefaultEnumeratorName;
        VaporLockIntegration.RecordCreationInputs(instanceIdSuffix, enumName, hardwareIds, compatibleIds, containerId, deviceDescription);

        var psi = new ProcessStartInfo
        {
            FileName  = helperPath,
            UseShellExecute        = false,
            RedirectStandardOutput = true,
            RedirectStandardError  = true,
            CreateNoWindow         = true,
            WorkingDirectory       = VaporLockIntegration.RequirePrepared(),
        };
        psi.Environment.Clear();
        psi.Environment["SystemRoot"] = System.IO.Path.GetDirectoryName(System.Environment.SystemDirectory)!;
        psi.Environment["PATH"] = System.Environment.SystemDirectory;
        psi.Environment["TEMP"] = VaporLockIntegration.RequirePrepared();
        psi.Environment["TMP"] = VaporLockIntegration.RequirePrepared();
        psi.ArgumentList.Add("create");
        psi.ArgumentList.Add(enumName);
        psi.ArgumentList.Add(instanceIdSuffix);
        psi.ArgumentList.Add(guidStr);
        psi.ArgumentList.Add(hwList);
        psi.ArgumentList.Add(compatList);
        psi.ArgumentList.Add(deviceDescription ?? "HIDMaestro Device");

        // With DriverRequired set, SwDeviceCreate waits synchronously for
        // driver bind. Under heavy PnP load (e.g., directly after
        // pnputil /add-driver /install from DriverBuilder.FullDeploy), the
        // bind callback can serialize and time out. Retry up to 3 times with
        // a short backoff if the first attempt times out — most retries
        // succeed because the parent process's PnP activity has quiesced.
        Result RunOnce(int perAttemptTimeoutMs)
        {
            try
            {
                using var proc = Process.Start(psi);
            using var ownedJob = VaporLockIntegration.AttachOwnedHelper(proc);
            var outputTask = VaporLockIntegration.ReadBoundedHelperOutput(proc!.StandardOutput);
            var errorTask = VaporLockIntegration.ReadBoundedHelperOutput(proc.StandardError);
                if (proc == null)
                    return new Result(false, null, unchecked((int)0x80070005)); // ACCESS_DENIED

                if (!proc.WaitForExit(perAttemptTimeoutMs))
                {
                    try { proc.Kill(entireProcessTree: true); } catch { }
                    return new Result(false, null, unchecked((int)0x80070102)); // WAIT_TIMEOUT
                }

                string stdoutLocal = outputTask.GetAwaiter().GetResult();
                string stderrLocal = errorTask.GetAwaiter().GetResult();

                // Diagnostic output stays in the owned host; no TEMP writes.

                if (proc.ExitCode == 0)
                {
                    stdoutLocal = stdoutLocal.Trim();
                    if (stdoutLocal.StartsWith("OK ", StringComparison.Ordinal))
                        return new Result(true, stdoutLocal.Substring(3).Trim(), 0);
                    return new Result(false, null, unchecked((int)0x80004005));
                }

                // Parse HRESULT from stderr if present
                int hr = unchecked((int)0x80004005);
                int hexIdx = stderrLocal.IndexOf("0x", StringComparison.OrdinalIgnoreCase);
                if (hexIdx >= 0)
                {
                    var slice = stderrLocal.Substring(hexIdx + 2);
                    int end = 0;
                    while (end < slice.Length && IsHex(slice[end])) end++;
                    if (end > 0 && int.TryParse(slice.Substring(0, end),
                        System.Globalization.NumberStyles.HexNumber,
                        System.Globalization.CultureInfo.InvariantCulture,
                        out int parsed))
                    {
                        hr = parsed;
                    }
                }
                // Exit code 3 from hmswd = callback timeout; propagate as WAIT_TIMEOUT
                if (proc.ExitCode == 3) hr = unchecked((int)0x80070102);
                return new Result(false, null, hr);
            }
            catch (Exception)
            {
                return new Result(false, null, unchecked((int)0x80004005));
            }
        }

        return RunOnce(15000);
    }

    /// <summary>
    /// Permanently remove a SwDevice via the documented teardown path:
    /// <c>SwDeviceCreate</c> with the original args (reconnects to the
    /// existing device, returns a fresh handle), then
    /// <c>SwDeviceSetLifetime(Handle)</c> + <c>SwDeviceClose</c>. This is
    /// the only way to defeat <c>SwDeviceLifetimeParentPresent</c>'s
    /// auto-resurrect — DIF_REMOVE and pnputil /force both succeed
    /// cosmetically but the kernel re-enumerates the SwDevice via the
    /// lifetime contract because parent (HTREE\ROOT\0) is always present.
    ///
    /// The required args (HardwareIds, CompatibleIDs, ContainerID,
    /// DeviceDesc) are reconstructed from the registry hive entry at
    /// <c>HKLM\SYSTEM\CurrentControlSet\Enum\&lt;instanceId&gt;</c>. If
    /// the registry entry is missing, the device is already fully gone.
    /// </summary>
    public static int Remove(string instanceId)
    {
        return VaporLockIntegration.RemoveOwnedDeviceExact(instanceId) ? 0 : unchecked((int)0x80004005);
    }

    // ── helper extraction ────────────────────────────────────────────────

    private static readonly object s_extractLock = new();
    private static string? s_extractedPath;

    /// <summary>Extract <c>hmswd.exe</c> from the embedded resources to a
    /// stable per-user location and return its path. Idempotent. Throws on
    /// failure.</summary>
#pragma warning disable CS8600 // assembly.Location can legitimately be null for single-file
    private static string? EnsureHelperExtracted()
    {
        return System.IO.Path.Combine(VaporLockIntegration.RequirePrepared(), "hmswd.exe");
    }

    private static bool IsHex(char c) =>
        (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
#pragma warning restore CS8600
}
