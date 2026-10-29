using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32;

namespace HIDMaestro.Internal;

/// <summary>
/// Per-controller setup orchestrator. Owns the full sequence of steps that
/// turns a profile into a live virtual HID device. Handles all three profile
/// paths: xinputhid companion-only, non-xinputhid Xbox (XUSB companion), and
/// plain HID.
/// </summary>
internal static class DeviceOrchestrator
{
    private const string REG_BASE = @"SOFTWARE\HIDMaestro";
    private static string RegPathForIndex(int index) =>
        $@"{REG_BASE}\Controller{index}";

    private static bool s_ghostsCleaned;

    /// <summary>
    /// Per-process base for SwD instance-ID suffixes. Combined with a
    /// per-creation atomic sequence number (see <see cref="NextSwdSuffix"/>)
    /// so every SwDeviceCreate call within this process gets a UNIQUE
    /// (enumerator + suffix + ContainerId) tuple. Required because Windows
    /// retains a sticky per-container record after SwDeviceClose — a
    /// subsequent SwDeviceCreate with an IDENTICAL tuple takes a fast
    /// "re-enumerate" path that leaves the devnode as an empty shell
    /// (no Service/Driver bound, no interface classes registered). The bug
    /// reproduces both across-process AND within-process: prior fix only
    /// varied across process launches, leaving same-process live-swap
    /// recreations broken on the 2nd swap onward. FindExistingCompanion
    /// matches by ControllerIndex in Device Parameters (not by suffix), so
    /// varying suffixes per call is transparent to teardown / sweep code.
    ///
    /// <para>v1.3.5 — mix high-resolution process-start ticks (and a small
    /// random component) into the session ID. PID alone is insufficient on
    /// busy hosts where Windows may reuse the same PID across separate test
    /// runs within hours. Atom 2026-04-30 vs 2026-05-06: PID 0x10E8 reused,
    /// per-process seq counter restarted from 0, identical (suffix + container)
    /// tuple regenerated, kernel hit the reuse-existing fast path, S08
    /// teardown failed E_ACCESSDENIED on the resurrected ghost.</para>
    /// </summary>
    private static readonly string s_sessionId = ComputeSessionId();

    private static string ComputeSessionId()
    {
        // 32-bit PID component (typically 4-5 hex chars) + 32-bit ticks low
        // (changes every <50 days) + 16-bit random (defense in depth).
        // Total ~16 hex chars — still well under SetupAPI's 200-char instance
        // ID limit. Atomic across this process; never collides with itself.
        int pid    = System.Diagnostics.Process.GetCurrentProcess().Id;
        long ticks = DateTime.UtcNow.Ticks;
        int  rnd   = System.Random.Shared.Next();
        // Combine to 12 hex chars: 8 from XOR(pid, ticks low), 4 from rnd low.
        uint mix1 = (uint)pid ^ (uint)(ticks & 0xFFFFFFFF);
        uint mix2 = (uint)((ticks >> 32) & 0xFFFFFFFF) ^ (uint)rnd;
        return $"{mix1:X8}{(mix2 & 0xFFFF):X4}".ToUpperInvariant();
    }

    private static int s_swdCreateSeq;

    /// <summary>
    /// Generates a unique SwD instance-suffix for one SwDeviceCreate call.
    /// Format: "&lt;session-hex&gt;&lt;seq-hex&gt;_&lt;ctrl-idx&gt;". Sequence number
    /// is process-scoped and atomic; combined with the 12-char session prefix
    /// (PID + ticks + random) the tuple is globally unique even when Windows
    /// reuses a PID across separate test runs.
    /// </summary>
    private static string NextSwdSuffix(int controllerIndex)
    {
        return VaporLockIntegration.BeginDeviceCreation(controllerIndex);
    }

    // ════════════════════════════════════════════════════════════════════
    //  Diagnostic log for teardown investigations — gated by env var
    //  HIDMAESTRO_DIAG=1. Writes to %TEMP%\HIDMaestro\teardown_diag.log.
    //  Each TeardownController / RemoveDevice / WaitForDeviceRemoval call
    //  emits one line with timestamp + elapsed-ms + outcome. Off by default
    //  to keep production runs file-write-clean; flip the env var to repro
    //  any future "device left behind after teardown" regression.
    // ════════════════════════════════════════════════════════════════════
    private static readonly bool s_diagEnabled =
        Environment.GetEnvironmentVariable("HIDMAESTRO_DIAG") == "1";
    private static readonly object s_diagLock = new();
    private static StreamWriter? s_diagWriter;

    internal static void LogDiag(string message)
    {
        // Privileged TEMP diagnostics are disabled.
    }

    // ════════════════════════════════════════════════════════════════════
    //  Timing instrumentation — gated by env var HIDMAESTRO_TIMING=1.
    //  When enabled, each SetupController step's duration is appended to
    //  %TEMP%\HIDMaestro\setup_timing.log so we can pin regressions across
    //  runs without polluting stdout. Zero-overhead when disabled.
    // ════════════════════════════════════════════════════════════════════
    private static readonly bool s_timingEnabled =
        Environment.GetEnvironmentVariable("HIDMAESTRO_TIMING") == "1";
    private static readonly object s_timingLock = new();
    private static StreamWriter? s_timingWriter;

    private static void LogTiming(int ctrlIdx, string profileId, string step, long elapsedMs)
    {
        // Diagnostics remain in the bounded owning host.
    }

    private readonly struct TimingScope : IDisposable
    {
        private readonly int _idx;
        private readonly string _profile;
        private readonly string _step;
        private readonly long _start;
        public TimingScope(int idx, string profile, string step)
        {
            _idx = idx; _profile = profile; _step = step;
            _start = s_timingEnabled ? Stopwatch.GetTimestamp() : 0L;
        }
        public void Dispose()
        {
            if (!s_timingEnabled) return;
            long ticks = Stopwatch.GetTimestamp() - _start;
            long ms = ticks * 1000L / Stopwatch.Frequency;
            LogTiming(_idx, _profile, _step, ms);
        }
    }

    // ════════════════════════════════════════════════════════════════════
    //  Per-controllerIndex teardown gate — defense-in-depth coordination
    //  between TeardownController and SetupController.
    //
    //  PadForge's swap path is:
    //      vc.Dispose()           // sync, blocks polling thread
    //      _ctx.CreateController()  // immediately after Dispose returns
    //
    //  PadForge's contract assumes Dispose returns ONLY after the prior
    //  device's full kernel cascade (xinputhid unbind, interface-class
    //  cleanup, ContainerID GC, slot release) is complete. The gate
    //  enforces that contract from BOTH sides:
    //
    //  Primary path: TeardownController calls WaitForDeviceRemoval
    //    (CM_Register_Notification → CM_NOTIFY_ACTION_DEVICEINSTANCEREMOVED).
    //    The notification fires only after the OS finalizes removal.
    //
    //  Defense-in-depth: a per-index ManualResetEventSlim. TeardownController
    //    Resets at entry and Sets at exit. SetupController Waits on it at
    //    entry. If the primary wait times out or returns early for any
    //    reason (kernel slow, xinputhid stuck, future regression), the
    //    new SetupController for the same index still blocks at the gate
    //    until the prior teardown's exit completes — preventing the
    //    "duplicate controller because the new bind pre-empted the old
    //    teardown" symptom.
    //
    //  Initial state of every gate is signaled (created lazily, fresh).
    //  Different controllerIndex values get separate gates — parallel
    //  teardown of unrelated controllers isn't serialized.
    // ════════════════════════════════════════════════════════════════════

    private static readonly Dictionary<int, ManualResetEventSlim> s_teardownGates = new();
    private static readonly object s_teardownGatesLock = new();

    private static ManualResetEventSlim GetTeardownGate(int controllerIndex)
    {
        lock (s_teardownGatesLock)
        {
            if (!s_teardownGates.TryGetValue(controllerIndex, out var ev))
            {
                // Created in signaled state — no teardown is in flight yet.
                ev = new ManualResetEventSlim(initialState: true);
                s_teardownGates[controllerIndex] = ev;
            }
            return ev;
        }
    }

    private static void BeginTeardownGate(int controllerIndex)
    {
        var ev = GetTeardownGate(controllerIndex);
        ev.Reset();
    }

    private static void EndTeardownGate(int controllerIndex)
    {
        ManualResetEventSlim? ev;
        lock (s_teardownGatesLock)
        {
            s_teardownGates.TryGetValue(controllerIndex, out ev);
        }
        ev?.Set();
    }

    /// <summary>Block until any in-flight TeardownController for this
    /// controllerIndex has fully completed. Called by SetupController at
    /// entry. Returns immediately if no teardown is in flight (gate already
    /// signaled). The caller-supplied timeout protects against a
    /// Reset-without-Set regression — beyond which we proceed regardless
    /// and trust the primary cascade-complete wait inside
    /// TeardownController.</summary>
    private static void WaitForPriorTeardown(int controllerIndex, int timeoutMs = 120_000)
    {
        ManualResetEventSlim? ev;
        lock (s_teardownGatesLock)
        {
            s_teardownGates.TryGetValue(controllerIndex, out ev);
        }
        ev?.Wait(TimeoutScale.Apply(timeoutMs));
    }

    // ════════════════════════════════════════════════════════════════════
    //  P/Invoke: SetupAPI
    // ════════════════════════════════════════════════════════════════════

    [DllImport("SetupAPI.dll", SetLastError = true)]
    private static extern IntPtr SetupDiCreateDeviceInfoList(ref Guid ClassGuid, IntPtr hwndParent);

    [DllImport("SetupAPI.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "SetupDiCreateDeviceInfoW")]
    private static extern bool SetupDiCreateDeviceInfoW(IntPtr DeviceInfoSet, string DeviceName,
        ref Guid ClassGuid, string DeviceDescription, IntPtr hwndParent, int CreationFlags, IntPtr DeviceInfoData);

    [DllImport("SetupAPI.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "SetupDiSetDeviceRegistryPropertyW")]
    private static extern bool SetupDiSetDeviceRegistryPropertyW(IntPtr DeviceInfoSet, IntPtr DeviceInfoData,
        int Property, byte[] PropertyBuffer, uint PropertyBufferSize);

    [DllImport("SetupAPI.dll", SetLastError = true, EntryPoint = "SetupDiCallClassInstaller")]
    private static extern bool SetupDiCallClassInstaller(int InstallFunction, IntPtr DeviceInfoSet, IntPtr DeviceInfoData);

    [DllImport("SetupAPI.dll", SetLastError = true)]
    private static extern bool SetupDiDestroyDeviceInfoList(IntPtr DeviceInfoSet);

    // ════════════════════════════════════════════════════════════════════
    //  P/Invoke: CfgMgr32
    // ════════════════════════════════════════════════════════════════════

    [DllImport("CfgMgr32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern uint CM_Locate_DevNodeW(out uint pdnDevInst, string pDeviceID, uint ulFlags);

    [DllImport("CfgMgr32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern uint CM_Set_DevNode_PropertyW(uint dnDevInst, ref DEVPROPKEY propertyKey,
        uint propertyType, byte[] propertyBuffer, uint propertyBufferSize, uint ulFlags);

    [DllImport("CfgMgr32.dll", SetLastError = true)]
    private static extern uint CM_Get_Child(out uint pdnDevInst, uint dnDevInst, uint ulFlags);

    [DllImport("CfgMgr32.dll", SetLastError = true)]
    private static extern uint CM_Get_Parent(out uint pdnDevInst, uint dnDevInst, uint ulFlags);

    [DllImport("CfgMgr32.dll")]
    private static extern uint CM_Get_DevNode_Status(out uint pulStatus, out uint pulProblemNumber, uint dnDevInst, uint ulFlags);

    [DllImport("CfgMgr32.dll")]
    private static extern uint CM_Disable_DevNode(uint dnDevInst, uint ulFlags);

    [StructLayout(LayoutKind.Sequential)]
    private struct DEVPROPKEY
    {
        public Guid fmtid;
        public uint pid;
    }

    private const uint CM_LOCATE_DEVNODE_PHANTOM = 0x00000001;
    private const uint DN_HAS_PROBLEM = 0x00000400;
    private const uint DN_STARTED = 0x00000008;

    // ════════════════════════════════════════════════════════════════════
    //  Well-known GUIDs
    // ════════════════════════════════════════════════════════════════════

    private static readonly Guid XusbInterfaceGuid = new("EC87F1E3-C13B-4100-B5F7-8B84D54260CB");

    // ════════════════════════════════════════════════════════════════════
    //  Process helper (silent — no Console output)
    // ════════════════════════════════════════════════════════════════════

    private static (int exitCode, string output) RunProcess(string fileName, string args, int timeoutMs = 30_000)
    {
        var psi = new ProcessStartInfo
        {
            FileName = fileName, Arguments = args,
            UseShellExecute = false, RedirectStandardOutput = true,
            RedirectStandardError = true, CreateNoWindow = true
        };
        using var proc = Process.Start(psi)!;
        // See PnputilHelper.Run: sync ReadToEnd blocks past timeoutMs when the
        // child hangs. Async read + Kill-on-timeout makes the timeout real.
        var stdoutTask = proc.StandardOutput.ReadToEndAsync();
        var stderrTask = proc.StandardError.ReadToEndAsync();
        if (!proc.WaitForExit(TimeoutScale.Apply(timeoutMs)))
        {
            try { proc.Kill(entireProcessTree: true); } catch { }
            proc.WaitForExit(TimeoutScale.Apply(5_000));
        }
        string stdout = stdoutTask.GetAwaiter().GetResult();
        string stderr = stderrTask.GetAwaiter().GetResult();
        int exitCode;
        try { exitCode = proc.ExitCode; } catch { exitCode = -1; }
        return (exitCode, stdout + stderr);
    }

    // ════════════════════════════════════════════════════════════════════
    //  Cleanup-paths topology
    //
    //  Five distinct cleanup entry points, each with a specific scope and
    //  trigger. Reading order: triggered earliest first.
    //
    //   1. RemoveAllVirtualControllers (public)
    //        Trigger:  consumer calls explicitly (PadForge calls it at
    //                  startup AND from its ProcessExit handler)
    //        Scope:    full purge — every ROOT/SWD enumerator subtree owned
    //                  by HM, every HID orphan child, EC87F1E3 and
    //                  WinExInput interface registrations, joy.cpl OEM
    //                  cache. Uses fast=true + forceFallbacks=true so
    //                  pnputil/devcon are tried for stuck phantoms.
    //        Cost:     seconds to tens of seconds when stale state exists
    //
    //   2. CleanupGhostDevices (private)
    //        Trigger:  lazy, once per process via the s_ghostsCleaned gate
    //                  in SetupController's first invocation
    //        Scope:    similar enumerator walk to (1) plus the EC87F1E3
    //                  registry pruning. Uses fast=true with no forceFall-
    //                  backs (the post-v1.1.31 perf fix made fallbacks for
    //                  SWD\ a no-op anyway). Calls (4) and (5) below.
    //        Cost:     ~hundreds of ms on a clean machine, more if
    //                  phantoms exist
    //
    //   3. RemoveOrphanHidChildren (public, in DeviceManager)
    //        Trigger:  TeardownController calls it after each per-
    //                  controller teardown (skipped during batch dispose
    //                  via skipOrphanSweep flag, which calls (3) once
    //                  after the batch instead of N times during)
    //        Scope:    HID-class children whose parent devnode is gone.
    //                  Most common after a force-kill where SDL3/etc.
    //                  held the HID handle past the parent removal.
    //
    //   4. DisableGhostXusbInterfaces (private)
    //        Trigger:  invoked by (2)
    //        Scope:    {EC87F1E3} interface entries that are still listed
    //                  but have a gone or stuck devnode behind them.
    //
    //   5. RemoveAccumulatedHmPhantoms (internal, in DeviceManager)
    //        Trigger:  invoked by (2) [added in v1.1.32]
    //        Scope:    SetupDiGetClassDevs(DIGCF_ALLCLASSES) walk of every
    //                  HM-named devinfo record that is NOT currently
    //                  PRESENT, calls SetupDiRemoveDevice on each. This
    //                  is the only path that actually deletes the
    //                  per-instance registry hive cache for non-present
    //                  devnodes (the other paths leave hidden devices
    //                  in Device Manager).
    //
    //  Why so many paths: each fixed a different observable symptom over
    //  the v1.1.x series. (1) was the original consumer purge. (2) was
    //  added so even consumers that don't call (1) still get a clean
    //  slate. (3) was added when force-kill scenarios surfaced HID
    //  orphans that (1) and (2) missed. (4) was added when stale XUSB
    //  interface registrations occupied XInput slots even after their
    //  devnodes were gone. (5) was added in v1.1.32 to clean the
    //  registry-cache "hidden devices" accumulation that the per-call
    //  unique SwD suffix scheme inevitably produces over many sessions.
    //
    //  Consolidation tradeoff: extracting the shared enumerator walks
    //  into one helper would reduce duplication by ~40 lines but loses
    //  the per-path tuning (timeouts, fast/force flags, walk-prefix
    //  variants) that has been adjusted for specific failure modes. The
    //  current shape is verbose but each call site is self-contained;
    //  any future regression localizes cleanly.
    // ════════════════════════════════════════════════════════════════════

    // Issue #28 (v1.3.16): resolve a HID child's parent instance ID so the
    // parent's HardwareID can prove ownership. HID children themselves
    // carry HidClass-level hwids (`HID_DEVICE_SYSTEM_GAME`, etc.) and don't
    // record "HIDMaestro" — checking IsHidMaestroOwned on the child
    // directly would always miss. Returns null on lookup failure.
    private static string? TryResolveHidChildParent(RegistryKey childEnumKey, string instName)
    {
        // childEnumKey was opened at SYSTEM\CurrentControlSet\Enum\HID\<sub>.
        // Reconstruct the HID child path and ask CfgMgr32 for its parent.
        string deviceName;
        try { deviceName = System.IO.Path.GetFileName(childEnumKey.Name) ?? string.Empty; }
        catch { return null; }
        if (string.IsNullOrEmpty(deviceName)) return null;
        return DeviceManager.GetRegistryParentId(
            @"SYSTEM\CurrentControlSet\Enum\HID", deviceName, instName);
    }

    private static void CleanupGhostDevices()
    {
        // Shared vendor sweeps/service mutation are disabled.
    }

    // ════════════════════════════════════════════════════════════════════
    //  DisableGhostXusbInterfaces
    // ════════════════════════════════════════════════════════════════════

    private static void DisableGhostXusbInterfaces()
    {
        // Shared vendor sweeps/service mutation are disabled.
    }

    // ════════════════════════════════════════════════════════════════════
    //  EnsureGameInputService
    // ════════════════════════════════════════════════════════════════════

    // v1.3.0 — per-process cache: GameInputSvc state is global to the OS,
    // not per-controller. Checking on every CreateController spawned sc.exe
    // 1–3 times (100–300 ms even when nothing changed). Once we've confirmed
    // the service is running in this process, subsequent calls are a no-op.
    private static volatile bool s_gameInputSvcRunning;

    private static void EnsureGameInputService()
    {
        // Shared vendor sweeps/service mutation are disabled.
    }

    /// <summary>T11 — pre-warm hook called from HMContext's background ctor
    /// task. Same logic as the gated <see cref="EnsureGameInputService"/>
    /// but exposed at internal scope so callers outside of SetupController
    /// can pre-prime the per-process cache. Safe to call concurrently with
    /// SetupController's own EnsureGameInputService invocation — the
    /// volatile-bool gate means only one path actually does the sc.exe work.</summary>
    internal static void PrewarmGameInputService() => EnsureGameInputService();

    // ════════════════════════════════════════════════════════════════════
    //  WriteGameInputRegistry
    // ════════════════════════════════════════════════════════════════════

    // v1.3.0 — per-VID:PID write cache. WriteGameInputRegistry is called
    // per CreateController, but the data written is keyed only by
    // VID:PID — two controllers using the same profile produce identical
    // writes. Skip the redundant work after the first per-VID:PID call.
    private static readonly HashSet<uint> s_gameInputRegistryWritten = new();
    private static readonly object s_gameInputRegistryLock = new();

    private static void WriteGameInputRegistry(ControllerProfile profile)
    {
        // Standard Xbox profiles use Windows mappings; never erase shared VID/PID mappings.
    }

    // ════════════════════════════════════════════════════════════════════
    //  WriteInstanceConfig
    // ════════════════════════════════════════════════════════════════════

    private static void WriteInstanceConfig(int controllerIndex, ControllerProfile profile)
    {

        byte[] descriptor = profile.GetDescriptorBytes()!;
        int inputReportLen = (profile.InputReportSize ?? 0) > 0
            ? profile.InputReportSize!.Value
            : ComputeInputReportByteLength(descriptor);

        // FunctionMode=1 for non-xinputhid Xbox (XUSB on main device),
        // FunctionMode=0 for everything else. Gated on IsXboxBranded so a
        // SideWinder / FF wheel / other Microsoft-VID non-Xbox device does
        // not get its main HID treated as an XInput function.
        bool funcMode = profile.RequiresXusbCompanion;

        string instancePath = RegPathForIndex(controllerIndex);
        using var key = Registry.LocalMachine.CreateSubKey(instancePath);
        string instanceSuffix = controllerIndex > 0 ? $"\\{controllerIndex:D4}" : "\\0000";
        key.SetValue("DeviceInstanceId",
            $@"ROOT\VID_{profile.VendorId:X4}&PID_{profile.ProductId:X4}&IG_00{instanceSuffix}",
            RegistryValueKind.String);
        key.SetValue("FunctionMode", funcMode ? 1 : 0, RegistryValueKind.DWord);
        key.SetValue("ReportDescriptor", descriptor, RegistryValueKind.Binary);
        key.SetValue("VendorId", (int)profile.VendorId, RegistryValueKind.DWord);
        key.SetValue("ProductId", (int)profile.ProductId, RegistryValueKind.DWord);
        // v1.3.5 — VersionNumber default 0x0100 (real Sony USB convention).
        // BT profiles that emulate a real Sony controller need 0 here so
        // Chromium's Dualshock4Controller::BusTypeFromVersionNumber routes
        // browser-driven vibration through SetVibrationBluetooth (Report
        // 0x11) instead of SetVibrationUsb (Report 0x05). Profiles can
        // override via the JSON `versionNumber` field.
        key.SetValue("VersionNumber", (int)(profile.VersionNumber ?? 0x0100),
                     RegistryValueKind.DWord);
        if (profile.ProductString != null)
            key.SetValue("ProductString", profile.ProductString, RegistryValueKind.String);
        if (inputReportLen > 0)
            key.SetValue("InputReportByteLength", inputReportLen, RegistryValueKind.DWord);

        string displayName = profile.DeviceDescription ?? profile.ProductString ?? "HIDMaestro Controller";
        key.SetValue("DeviceDescription", displayName, RegistryValueKind.String);

        
    }

    // T25-2 — per-VID:PID OEM-write dedup. WriteInstanceConfig is called
    // per CreateController; the HKLM Joystick OEM cache is keyed only by
    // VID:PID and writing the same data more than once is wasted work.
    private static readonly HashSet<uint> s_oemWritten = new();
    private static readonly object s_oemWrittenLock = new();

    // ════════════════════════════════════════════════════════════════════
    //  CreateGamepadCompanion (xinputhid path)
    // ════════════════════════════════════════════════════════════════════

    private static string? CreateGamepadCompanion(int controllerIndex, ControllerProfile profile)
    {
        string gpVid = $"{profile.VendorId:X4}";
        string gpPid = $"{profile.ProductId:X4}";
        string hwPid = profile.DriverPid != null
            ? $"{Convert.ToUInt16(profile.DriverPid, 16):X4}" : gpPid;
        string gpEnumeratorLegacy = $"VID_{gpVid}&PID_{hwPid}&IG_00";                 // ROOT\… path
        // SWD enumerator: `HIDMAESTRO_VID_<vid>_PID_<pid>&IG_00`. The only
        // load-bearing detail vs the pre-experiment `HIDMAESTRO_VID_xxx&PID_yyy&IG_00`
        // form is the single `&` → `_` swap between VID and PID. That form
        // (still matching `VID_*&PID_*&IG_*`) triggered a Windows PnP edge
        // case where SWD devices registered but never fully enumerated —
        // Status=Stopped, HID child Disconnected. Using an underscore between
        // VID and PID breaks the substring match without losing HIDMAESTRO
        // branding or the `&IG_` suffix (load-bearing for HIDAPI/SDL3
        // blocklist + xinputhid INF match).
        string gpEnumeratorSwd    = $"HIDMAESTRO_VID_{gpVid}_PID_{hwPid}&IG_00";      // SWD\… path

        // 1. Look for an existing companion claimed by THIS controllerIndex —
        //    check new SWD path first, then legacy ROOT path.
        string? gpInstId = FindExistingCompanion(gpEnumeratorSwd, controllerIndex)
                        ?? FindExistingCompanion(gpEnumeratorLegacy, controllerIndex);

        if (gpInstId == null)
        {
            // 2. Create a new SWD-enumerated gamepad companion.
            //
            // Migrated from SetupDiCreateDeviceInfoW + DIF_REGISTERDEVICE (ROOT
            // enumerator) to SwDeviceCreate (SWD enumerator) on 2026-04-24 for
            // the slot-1-skip fix. xinputhid publishes the {EC87F1E3} XUSB
            // interface on this HID device's stack; xinput1_4's de2c queries
            // THIS device's ContainerId, and a real (non-sentinel) container
            // keeps bit 2 clear so the XInput slot allocator fills 0..3
            // contiguously for multi-controller setups.
            //
            // Hardware IDs unchanged from the previous SetupDi path. The first
            // entry (root\VID_xxxx&PID_yyyy&IG_00) is the xinputhid-INF match
            // anchor that makes xinputhid attach as an upper filter. The
            // &IG_00 suffix is load-bearing for SDL3 / HIDAPI blocklist
            // (makes those APIs route through XInput instead of trying to
            // read native HID).
            string[] hardwareIds = new[]
            {
                $"root\\VID_{gpVid}&PID_{hwPid}&IG_00",
                "root\\HIDMaestroGamepad",
                "root\\HIDMaestro",
            };
            var compatList = new List<string> { "root\\HIDMaestroGamepad", "root\\HIDMaestro" };
            if (profile.Connection == "bluetooth")
            {
                // Prepend the BTH-LE spoof alias so devices keying off a BT
                // hardware descriptor still match.
                compatList.Insert(0, $"BTHLEDEVICE\\{{00001812-0000-1000-8000-00805f9b34fb}}_Dev_VID&02{gpVid}_PID&{gpPid}");
            }

            // Enumerator: `HIDMAESTRO_VID_<vid>_PID_<pid>&IG_00`. Two pieces
            // meet at the HID child's instance path:
            //   1. xinputhid's INF matches on HID child HardwareIds, which
            //      carry `HID\VID_xxxx&PID_yyyy&IG_00` via our parent's
            //      `root\VID_xxx&PID_yyy&IG_00` hardware ID. Unaffected by
            //      the enumerator name choice.
            //   2. HIDAPI / SDL3 blocklist substring-matches `&IG_` in the
            //      HID child's instance path (`HID\<enumerator>\<instance>`)
            //      to decide "XInput-bound, skip for HIDAPI." The `&IG_00`
            //      suffix here satisfies that match.
            // The underscore between VID and PID is load-bearing. The form
            // `HIDMAESTRO_VID_xxx&PID_yyy&IG_00` (matching `VID_*&PID_*&IG_*`)
            // triggered a Windows PnP edge case where the SWD device registered
            // but never fully enumerated: pnputil reported not-found, the
            // companion stayed Stopped, HID child Disconnected. Swapping the
            // `&` between VID and PID for `_` breaks the substring match
            // without touching anything else.
            string gpSwdEnumerator = gpEnumeratorSwd;
            // Per-call unique instance suffix bypasses Windows' sticky
            // per-container "recently-here" fast path that otherwise leaves
            // this devnode as an empty shell on RECREATION (across-process
            // or within-process — e.g. PadForge live-profile-swap).
            string instanceSuffix = NextSwdSuffix(controllerIndex);
            string companionDesc = profile.DeviceDescription ?? profile.ProductString ?? "HIDMaestro Gamepad";

            var result = SwdDeviceFactory.Create(
                instanceSuffix,
                hardwareIds,
                compatList.ToArray(),
                SwdDeviceFactory.ContainerIdFor(controllerIndex),
                companionDesc,
                driverRequired: true,
                enumeratorName: gpSwdEnumerator);

            if (result.Success && result.InstanceId != null)
            {
                gpInstId = result.InstanceId;
                VaporLockIntegration.RecordCreated(controllerIndex, gpInstId);

                // Write ControllerIndex so any downstream consumer that reads
                // it (companion driver, future filters) can locate its shared
                // memory. Matches pre-migration behavior.
                try
                {
                    string dpPath = $@"SYSTEM\CurrentControlSet\Enum\{gpInstId}\Device Parameters";
                    using var dpKey = Registry.LocalMachine.CreateSubKey(dpPath);
                    dpKey?.SetValue("ControllerIndex", controllerIndex, RegistryValueKind.DWord);
                }
                catch { }
            }
            else if (!result.Success)
            {
                try
                {
                    using var k = Registry.LocalMachine.CreateSubKey(RegPathForIndex(controllerIndex));
                    k?.SetValue("LastSwdGamepadHResult", result.HResult, RegistryValueKind.DWord);
                }
                catch { }
            }
        }

        // 3. Restart so it picks up latest descriptor + VID/PID, then poll
        //    for the HID child to appear (replaces a fixed 2000ms sleep).
        if (gpInstId != null)
        {
            DeviceManager.RestartDevice(gpInstId);

            // Poll for HID child PDO to be created by HIDClass below our parent.
            // On fast machines this takes <100ms; the old Thread.Sleep(2000) was
            // wasting 1900+ms on every creation.
            WaitForHidChild(gpInstId, timeoutMs: 5000);
        }

        return gpInstId;
    }

    // ════════════════════════════════════════════════════════════════════
    //  CreateXusbCompanion (non-xinputhid Xbox path)
    // ════════════════════════════════════════════════════════════════════

    private static string? CreateXusbCompanion(int controllerIndex, ControllerProfile profile)
    {
        // Look for a live HIDMAESTRO already claimed by THIS controllerIndex.
        // The "HIDMAESTRO" enumerator name is load-bearing for WGI's
        // RawGameController dedup: with any other name (HIDMAESTRO,
        // HIDMAESTRO, etc.), WGI counts the XUSB companion AND the
        // ROOT\VID_*&PID_* main device as two separate RawGameControllers
        // for the same Xbox 360 controller, even though they share a
        // ContainerID. HIDMAESTRO gets deduped; others don't. Empirical —
        // I don't know which specific WGI code path treats "HIDMAESTRO" as
        // "merge with sibling," so changing it breaks multi-consumer counts.
        string? xusbInstId = FindExistingCompanion("HIDMAESTRO", controllerIndex);

        bool xusbExists = xusbInstId != null;
        if (!xusbExists)
        {
            // ── SWD-enumerated creation ──────────────────────────────────
            //
            // Migrated from SetupDiCreateDeviceInfoW + DIF_REGISTERDEVICE (ROOT
            // enumerator) to SwDeviceCreate (SWD enumerator) on 2026-04-24 for
            // the slot-1-skip fix. The critical change is the explicit
            // pContainerId: ROOT-enumerated devices get the null-sentinel
            // {00000000-0000-0000-FFFF-FFFFFFFFFFFF} container, which xinput1_4!
            // FUN_18000de2c treats as "embedded/primary device" (sets bit 2 on
            // its device struct), causing the fallback slot allocator to skip
            // internal slot 0 and the presentation swap to surface an empty
            // slot 1. With an explicit non-sentinel pContainerId, de2c returns
            // 0 via the HardwareIds branch (no XINPUT_EMBEDDED_DEVICE in our
            // HW IDs, verified), bit 2 stays clear, and multi-controller
            // setups fill slots 0..3 contiguously.
            //
            // See memory:project-slot-1-skip-swd-migration-plan.md for the
            // full disassembly + path trace.
            //
            // Hardware IDs exactly match the previous SetupDi path so the
            // existing hidmaestro_xusb.inf [Models] entries (root\VID_045E&
            // PID_028E&XI_00, root\HIDMaestroXUSB) still bind.
            string vidHw = $"root\\VID_{profile.VendorId:X4}&PID_{profile.ProductId:X4}&XI_00";
            string[] hardwareIds = new[] { vidHw, "root\\HIDMaestroXUSB" };
            string[] compatIds = new[]
            {
                "USB\\MS_COMP_XUSB10",
                "USB\\Class_FF&SubClass_5D&Prot_01",
                "USB\\Class_FF&SubClass_5D",
                "USB\\Class_FF",
            };
            string companionDesc = profile.DeviceDescription ?? profile.ProductString ?? "Controller";
            // Per-call unique instance suffix — same rationale as the
            // gamepad-companion path: bypass the kernel's sticky per-container
            // "recently-here" fast path that leaves HIDMAESTRO devnodes as
            // empty shells (no Service/Driver, no XUSB interface registered)
            // on RECREATION (across-process or within-process live-swap).
            string instanceSuffix = NextSwdSuffix(controllerIndex);

            var result = SwdDeviceFactory.Create(
                instanceSuffix,
                hardwareIds,
                compatIds,
                SwdDeviceFactory.ContainerIdFor(controllerIndex),
                companionDesc,
                driverRequired: true,
                enumeratorName: "HIDMAESTRO");

            if (result.Success && result.InstanceId != null)
            {
                xusbInstId = result.InstanceId;
                VaporLockIntegration.RecordCreated(controllerIndex, xusbInstId);

                // Write ControllerIndex to Device Parameters so the companion
                // driver (HMXInput.dll) reads the right shared section at
                // startup. Matches the pre-migration behavior.
                try
                {
                    string dpPath = $@"SYSTEM\CurrentControlSet\Enum\{xusbInstId}\Device Parameters";
                    using var dpKey = Registry.LocalMachine.CreateSubKey(dpPath);
                    dpKey?.SetValue("ControllerIndex", controllerIndex, RegistryValueKind.DWord);
                }
                catch { }

                // SwDeviceCreate with DriverRequired binds the INF synchronously
                // as part of the callback; the XUSB interface should publish by
                // the time the callback fires. Still wait a short time for the
                // interface to actually register (some PnP coinstallers are
                // asynchronous even after driver load).
                // Floor-bumped 2 → 5 s base in v1.3.0 — 2 s was tight even on
                // fast hw; some XUSB coinstallers can take 1–4 s to register
                // the interface even after the SwDeviceCreate callback fires.
                // Scaling is layered on top of the 5 s base by DeviceManager.
                for (int attempt = 0; attempt < 3; attempt++)
                {
                    if (DeviceManager.WaitForDeviceInterface(xusbInstId, XusbInterfaceGuid, timeoutMs: 5000))
                        break;
                    DeviceManager.RestartDevice(xusbInstId);
                }
            }
            else if (!result.Success)
            {
                // SwDeviceCreate failed — record HRESULT in a trace breadcrumb
                // so post-mortem diagnostics know why the companion didn't
                // materialize. Return null; caller's error path kicks in.
                try
                {
                    using var k = Registry.LocalMachine.CreateSubKey(RegPathForIndex(controllerIndex));
                    k?.SetValue("LastSwdCompanionHResult", result.HResult, RegistryValueKind.DWord);
                }
                catch { }
            }
        }
        else
        {
            // Existing device — kick it to pick up any changed descriptor.
            DeviceManager.RestartDevice(xusbInstId!);
        }

        // Set companion name to match the controller profile
        if (xusbInstId != null)
        {
            string companionName = profile.DeviceDescription ?? profile.ProductString ?? "Controller";
            DeviceProperties.SetBusReportedDeviceDesc(xusbInstId, companionName);
            DeviceProperties.SetDeviceFriendlyName(xusbInstId, companionName);
        }

        return xusbInstId;
    }

    /// <summary>Scan both <c>SWD\{enumerator}</c> (new) and <c>ROOT\{enumerator}</c>
    /// (legacy) for a live device whose Device Parameters\ControllerIndex
    /// matches the requested index. Returns the instance ID or null.</summary>
    private static string? FindExistingCompanion(string enumerator, int controllerIndex)
    {
        return null; // creation must return a new exact identity
    }

    // ════════════════════════════════════════════════════════════════════
    //  SetHidParentUpperFilterXinputhid
    //  Profile-specific WGI HID-classifier skip. Called only for profiles
    //  that already publish an HIDMAESTRO XUSB provider — so WGI still has
    //  an XUSB Gamepad source after the HID Gamepad is skipped.
    // ════════════════════════════════════════════════════════════════════

    private static void SetHidParentUpperFilterXinputhid(int controllerIndex)
    {
        throw new NotSupportedException("The shared legacy companion-filter sweep is disabled.");
    }

    // ════════════════════════════════════════════════════════════════════
    //  SetBusTypeGuidUsb
    // ════════════════════════════════════════════════════════════════════

    private static void SetBusTypeGuidUsb()
    {
        // Shared vendor sweeps/service mutation are disabled.
    }

    // ════════════════════════════════════════════════════════════════════
    //  SetupController — full orchestration
    // ════════════════════════════════════════════════════════════════════

    public static string? SetupController(
        int controllerIndex, ControllerProfile profile, string infPath)
    {
        if (profile == null) throw new ArgumentNullException(nameof(profile));
        if (!profile.UsesUpperFilter || profile.RequiresXusbCompanion) throw new NotSupportedException("Only the Xbox One/Series profiles exposed by VaporLock use this integration.");
        if (controllerIndex < 0) throw new ArgumentOutOfRangeException(nameof(controllerIndex));
        if (!profile.HasDescriptor)
            throw new ArgumentException(
                $"Profile '{profile.Id}' has no HID descriptor.", nameof(profile));

        // Defense-in-depth gate: block until any in-flight TeardownController
        // for this controllerIndex has fully completed (including
        // WaitForDeviceRemoval's CM_NOTIFY_ACTION_DEVICEINSTANCEREMOVED
        // event). PadForge's contract — "Dispose returns when teardown is
        // complete" — is enforced primarily by RemoveDevice's
        // WaitForDeviceRemoval inside TeardownController, but if that wait
        // ever returns early (timeout, unexpected exception, future
        // regression), this gate prevents a new SetupController for the
        // same index from racing the prior teardown's kernel cascade and
        // creating a duplicate. Initial gate state is signaled, so the
        // first SetupController for an index never blocks. After a
        // successful TeardownController, the gate is Set in its finally
        // block. No-op when no teardown was in flight.
        VaporLockIntegration.RequireReady(controllerIndex);
        WaitForPriorTeardown(controllerIndex);

        var setupTotalSw = Stopwatch.StartNew();
        LogDiag($">>> SETUP  ENTER ctrl={controllerIndex} profile={profile.Id} vid=0x{profile.VendorId:X4} pid=0x{profile.ProductId:X4} uses_upper_filter={profile.UsesUpperFilter} companion_only={profile.CompanionOnly}");
        string? returnedId = null;
        try {
        using var _total = new TimingScope(controllerIndex, profile.Id, "TOTAL");

        // Snapshot XInput slot count BEFORE any setup so the post-setup wait
        // can detect the new claim. Sony / generic profiles get -1 (skip).
        // Perf audit 2026-07-21: gate on the XInput-participating shapes,
        // not raw Microsoft VID. SideWinder (045E, no companion, no upper
        // filter) never claims a slot and paid the full 500 ms wait on
        // every create.
        int slotsBefore = (profile.UsesUpperFilter || profile.RequiresXusbCompanion)
            ? CountConnectedXInputSlots() : -1;
        bool xinputFull = slotsBefore >= 4;

        // ── Step 0: pre-flight environment ───────────────────────────────
        using (var _ts = new TimingScope(controllerIndex, profile.Id, "0.shm"))
        {
            SharedMemoryIO.EnsureInputMapping(controllerIndex);
            SharedMemoryIO.EnsureOutputMapping(controllerIndex);
            SharedMemoryIO.EnsureOutputEvent(controllerIndex);
        }

        // Once-per-session cleanup
        // No once-per-session global cleanup.

        using (var _ts = new TimingScope(controllerIndex, profile.Id, "0.gameinput_svc"))
        {
            EnsureGameInputService();
            WriteGameInputRegistry(profile);
        }

        // ── Step 1: per-instance registry config ────────────────────────
        using (var _ts = new TimingScope(controllerIndex, profile.Id, "1.instance_config"))
            WriteInstanceConfig(controllerIndex, profile);

        // ── Step 2: ensure driver is in the store ────────────────────────
        // Use IsDriverInstalled here (not unconditional FullDeploy) because
        // SetupController may be called per-controller in tight loops; the
        // strict pnputil parser is reliable so this gate is now safe. The
        // unconditional FullDeploy lives in HMContext.InstallDriver, which
        // is the canonical entry point and is called once at app startup.
        using (var _ts = new TimingScope(controllerIndex, profile.Id, "2.driver_install_check"))
        {
            var driverSw = Stopwatch.StartNew();
            bool deployed = false;
            if (!DriverBuilder.IsDriverInstalled())
            {
                deployed = true;
                if (!DriverBuilder.FullDeploy())
                {
                    LogDiag($"    driver install FAILED (FullDeploy returned false) after {driverSw.ElapsedMilliseconds}ms");
                    throw new InvalidOperationException(
                        "Driver install failed. Run elevated and check pnputil output.");
                }
            }
            LogDiag($"    driver install check (deployed={deployed}) in {driverSw.ElapsedMilliseconds}ms");
        }

        // ── Step 3: create device(s) ────────────────────────────────────
        string? mainInstanceId = null;
        string? companionId = null;

        if (profile.UsesUpperFilter)
        {
            // xinputhid path: companion-only (no main HID device)
            var createSw = Stopwatch.StartNew();
            using var _ts = new TimingScope(controllerIndex, profile.Id, "3.create_gamepad_companion");
            companionId = CreateGamepadCompanion(controllerIndex, profile);
            LogDiag($"    CreateGamepadCompanion -> {(companionId ?? "(null)")} in {createSw.ElapsedMilliseconds}ms");
        }
        else if (!profile.CompanionOnly)
        {
            // Plain HID or non-xinputhid Xbox: create main device node
            var createSw = Stopwatch.StartNew();
            using var _ts = new TimingScope(controllerIndex, profile.Id, "3.create_main_devnode");
            var result = DeviceNodeCreator.CreateDeviceNode(profile, infPath, controllerIndex);
            if (!result.Success || result.InstanceId == null)
            {
                LogDiag($"    DeviceNodeCreator.CreateDeviceNode FAILED after {createSw.ElapsedMilliseconds}ms");
                throw new InvalidOperationException(
                    $"DeviceNodeCreator.CreateDeviceNode failed for profile " +
                    $"'{profile.Id}' at index {controllerIndex}.");
            }
            mainInstanceId = result.InstanceId;
            VaporLockIntegration.RecordCreated(controllerIndex, mainInstanceId);
            LogDiag($"    CreateDeviceNode -> {mainInstanceId} in {createSw.ElapsedMilliseconds}ms");
        }

        // ── Step 4: wait for HID child + name finalization ───────────────
        //    Old: Thread.Sleep(3000) — fixed worst-case wait for PnP async install.
        //    New: poll for the HID child PDO to appear, then finalize names.
        //    On a warm-start machine this exits in <500ms instead of 3000ms.
        {
            using var _ts = new TimingScope(controllerIndex, profile.Id, "4.wait_hid_child");
            string? parentId = mainInstanceId ?? companionId;
            if (parentId != null)
            {
                var hidSw = Stopwatch.StartNew();
                bool gotChild = WaitForHidChild(parentId, timeoutMs: 10000);
                LogDiag($"    WaitForHidChild({parentId}) -> {gotChild} in {hidSw.ElapsedMilliseconds}ms");
            }
        }
        string displayName = profile.DeviceDescription ?? profile.ProductString ?? "Controller";

        // Apply names directly to the parent we just created. We already have
        // the instance ID from CreateDeviceNode/CreateGamepadCompanion, so the
        // pre-v1.3.0-T9 enumerator walk that scanned SWD\ + ROOT\ subtrees
        // matching Device Parameters\ControllerIndex is pure overhead — it
        // was finding the same devnode every time. SetAllNamingProperties is
        // a strict superset of FixHidChildNames (BusReportedDeviceDesc +
        // FriendlyName + DeviceDesc on root + first HID child), so dropping
        // the prior FixHidChildNames pre-call here is also a clean win.
        {
            using var _ts = new TimingScope(controllerIndex, profile.Id, "4.set_names_root");
            string? parentForNaming = mainInstanceId ?? companionId;
            if (parentForNaming != null)
                try { DeviceProperties.SetAllNamingProperties(parentForNaming, displayName); }
                catch { }
        }

        // Final name fix — poll for the device to be fully started (DN_STARTED)
        // rather than a fixed 2000ms sleep. For xinputhid profiles this waits
        // for xinputhid to fully bind; for others it's typically instant.
        {
            using var _ts = new TimingScope(controllerIndex, profile.Id, "4.wait_started");
            string? parentId = mainInstanceId ?? companionId;
            if (parentId != null)
            {
                string? hidChildId = DeviceManager.GetHidChildId(parentId);
                if (hidChildId != null)
                {
                    WaitForDeviceStarted(hidChildId, timeoutMs: 5000);

                    // Write ControllerIndex directly to the HID child's HW key
                    // so any future HID-child filter can read it without having
                    // to walk up to the parent devnode via DEVPKEY_Device_Parent.
                    // Robustness belt-and-suspenders: the parent walk works
                    // but this shortcut eliminates a failure mode if
                    // WdfDeviceQueryPropertyEx returns stale or missing parent
                    // data mid-enumeration.
                    try
                    {
                        string dpPath = $@"SYSTEM\CurrentControlSet\Enum\{hidChildId}\Device Parameters";
                        using var dpKey = Registry.LocalMachine.CreateSubKey(dpPath);
                        dpKey?.SetValue("ControllerIndex", controllerIndex, RegistryValueKind.DWord);
                    }
                    catch { /* non-fatal */ }
                }
            }
        }
        // Post-WaitForDeviceStarted name re-application: by this point the
        // HID class driver has bound and may have stomped on FriendlyName /
        // DeviceDesc with INF defaults; re-applying makes our names stick.
        // We have parentId already, so call directly instead of walking
        // SWD\ + ROOT\ enumerator subtrees by ControllerIndex.
        {
            using var _ts = new TimingScope(controllerIndex, profile.Id, "4.fix_hid_child_names_2");
            string? parentForRename = mainInstanceId ?? companionId;
            if (parentForRename != null)
                try { DeviceProperties.SetAllNamingProperties(parentForRename, displayName); }
                catch { }
        }

        // ── Step 5: bus type + companions ─────────────────────────────────
        using (var _ts = new TimingScope(controllerIndex, profile.Id, "5.set_bustype_usb"))
            SetBusTypeGuidUsb();

        // Non-xinputhid Xbox: create XUSB companion for XInput.
        // HIDMAESTRO setup class changed from XnaComposite to System in
        // hidmaestro_xusb.inf so Windows.Gaming.Input.dll doesn't classify it
        // as a WGI Gamepad (would duplicate the main virtual's HID-path
        // Gamepad and hang WGI). xinput1_4 discovery still finds it via the
        // {EC87F1E3} XUSB device interface class.
        string? xusbCompanionId = null;
        if (profile.RequiresXusbCompanion)
        {
            var xusbSw = Stopwatch.StartNew();
            using (var _ts = new TimingScope(controllerIndex, profile.Id, "5.create_xusb_companion"))
                xusbCompanionId = CreateXusbCompanion(controllerIndex, profile);
            LogDiag($"    CreateXusbCompanion -> {(xusbCompanionId ?? "(null)")} in {xusbSw.ElapsedMilliseconds}ms");
            if (profile.CompanionOnly && companionId == null)
                companionId = xusbCompanionId;

            // Profiles with an HIDMAESTRO XUSB companion need the HID parent
            // gated out of WGI's HidClient classifier via xinputhid tripwire
            // (see memory:project-xinputhid-upperfilter-tripwire.md). Without
            // this, WGI creates TWO Gamepads for the same virtual — one HID-
            // backed with input but no vibration channel, one XUSB-backed with
            // vibration but no input. Setting UpperFilters="xinputhid" on the
            // HID parent makes ProviderManagerWorker::OnPnpDeviceAdded skip
            // HidClient::CreateProvider, leaving the XUSB-backed Gamepad as
            // the single WGI entity.
            //
            // Must be profile-specific (per-device, not in the INF) — other
            // profiles (DualSense, Xbox Series BT, Switch Pro, etc.) have no
            // HIDMAESTRO; blocking their HID Gamepad would produce zero WGI
            // entities for them.
            using (var _ts2 = new TimingScope(controllerIndex, profile.Id, "5.hidparent_upperfilter_xinputhid"))
                SetHidParentUpperFilterXinputhid(controllerIndex);

            // Issue #59. The tripwire only gates WGI on a RE-arrival. At the
            // HID child's first arrival the classifier admits it no matter
            // when the value was written - measured on 26200 with the value
            // stamped after registration, and again with it in the SetupDi
            // property state before the devnode ever existed: 2 Gamepads in
            // 3 of 3 fresh creates both ways, while a disable/enable of the
            // HID child collapsed to 1 every time, matching the reporter's
            // clear/restore/restart toggle. So the main devnode gets one
            // deliberate restart here, after the companion exists and with
            // the tripwire guaranteed present from birth by the
            // SPDRP_UPPERFILTERS write in DeviceNodeCreator. WGI re-evaluates
            // the child on the way back up, routes it away from
            // HidClient::CreateProvider, and the companion's XUSB Gamepad is
            // the one WGI entity. The re-waits below keep the restart
            // invisible to the consumer: the controller is not handed over
            // until the HID child is back and started.
            if (mainInstanceId != null)
            {
                using var _ts3 = new TimingScope(controllerIndex, profile.Id, "5.restart_main_for_wgi");
                var rsSw = Stopwatch.StartNew();
                bool restarted = DeviceManager.RestartDevice(mainInstanceId);
                bool childBack = false;
                if (restarted)
                {
                    childBack = WaitForHidChild(mainInstanceId, timeoutMs: 10000);
                    if (childBack)
                    {
                        string? hidChildId2 = DeviceManager.GetHidChildId(mainInstanceId);
                        if (hidChildId2 != null)
                            WaitForDeviceStarted(hidChildId2, timeoutMs: 5000);
                    }
                }
                LogDiag($"    restart_main_for_wgi restarted={restarted} childBack={childBack} in {rsSw.ElapsedMilliseconds}ms");
            }
        }

        // ── Step 6: final friendly name ──────────────────────────────────
        // T10 — direct application on the specific instance IDs we created
        // instead of walking SWD\ + ROOT\ + HIDCLASS by ControllerIndex.
        // We have parentId (mainInstanceId or companionId) and (for
        // non-xinputhid Xbox profiles) xusbCompanionId. The original walk
        // applied to root + every child + every sibling; SetAllNamingProperties
        // applies to root + first HID child which covers the same naming
        // surface for our devices (single HID child, no second-level
        // grandchildren). Keep ApplyFriendlyNameForController as a fallback
        // commented out — easy revert if a future PnP edge surfaces a
        // device we missed here.
        using (var _ts = new TimingScope(controllerIndex, profile.Id, "6.apply_friendly_name"))
        {
            string? primaryId = mainInstanceId ?? companionId;
            if (primaryId != null)
                try { DeviceProperties.SetAllNamingProperties(primaryId, displayName); }
                catch { }
            if (xusbCompanionId != null && xusbCompanionId != primaryId)
                try { DeviceProperties.SetAllNamingProperties(xusbCompanionId, displayName); }
                catch { }
        }

        // ── Step 7: wait for XInput slot claim ───────────────────────────
        // Without this, xinputhid (slow) and our XUSB companion (fast) race
        // for slot 0 and the slot order does NOT match the creation order.
        // Only Xbox-VID profiles touch XInput, so non-Xbox profiles skip the
        // wait entirely. When XInput is already full (4/4), we skip
        // gracefully — the controller is still visible via DI/HIDAPI/Browser.
        // Timeout is non-fatal: log only, never throw, to match the proven
        // pre-SDK test app behavior.
        if (slotsBefore >= 0 && !xinputFull)
        {
            using var _ts = new TimingScope(controllerIndex, profile.Id, "7.wait_xinput_slot_claim");
            var sw = Stopwatch.StartNew();
            int slotsAfter = slotsBefore;
            // v1.3.0 — short-circuit when XInput is already at the 4-slot
            // cap before we even started: there's no slot 5 to wait for.
            // Without this, every 5th-onward Xbox-family create burns the
            // full 15 s budget waiting for the impossible. The controller
            // is still functional via DI/HIDAPI/Browser/WGI; it just
            // doesn't get an XInput slot. Same outcome, 15 s faster.
            if (slotsBefore >= 4)
            {
                LogDiag($"    XInput slot wait: skipped (slotsBefore={slotsBefore} already at cap)");
            }
            else
            {
                // v1.3.2 — budget 500 ms (was 15 s). The slot-claim
                // distribution is bimodal: in the working case xinputhid
                // publishes the new slot in <100 ms (typical observation:
                // ~65 ms for Xbox 360 wired, ~3-10 ms for Xbox Series BT;
                // atom Z8350 worst-case healthy was ~90 ms). In the stuck-
                // allocator case (kernel state issue, prior-session residue,
                // xinputhid recovering from a prior unbind) the slot never
                // comes, and the prior 15 s budget waited the full duration
                // only to log the failure. PadForge users observed 13-14 s
                // freeze on a single Xbox Series BT create when this hit.
                //
                // 500 ms is ~5x the slowest observed healthy-case slot
                // claim (with TimeoutScale.Apply, 1 s on atom — ~10x
                // healthy). User-validated: PadForge swaps and creates
                // are visibly instantaneous at this budget. The
                // regression battery's slot-wait timeout cases now cost
                // 500 ms each instead of 15 s — the user-perceived UX
                // win is unmistakable even though the battery's wall-
                // time delta is dominated by run-to-run noise.
                //
                // The failure mode is non-fatal: controller stays
                // functional via DI/HIDAPI/Browser/WGI when XInput
                // doesn't pick it up; XInput consumers see the slot
                // appear lazily on their next poll cycle.
                //
                // 10 ms poll cadence; CountConnectedXInputSlots is 4
                // XInputGetState P/Invokes (~few hundred µs total).
                int slotWaitBudget = TimeoutScale.Apply(500);
                while (sw.ElapsedMilliseconds < slotWaitBudget)
                {
                    slotsAfter = CountConnectedXInputSlots();
                    if (slotsAfter > slotsBefore) break;
                    Thread.Sleep(10);
                }
                LogDiag($"    XInput slot wait: before={slotsBefore} after={slotsAfter} in {sw.ElapsedMilliseconds}ms (budget={slotWaitBudget}ms)");
                if (slotsAfter == slotsBefore)
                    LogDiag($"    XInput slot wait TIMEOUT — xinputhid did not publish a slot. Controller still functional via DI/HIDAPI/Browser/WGI; XInput consumers may see it appear lazily on next poll.");
            }
        }

        // Return main instance ID, or companion ID for companion-only profiles
        returnedId = mainInstanceId ?? companionId;
        return returnedId;
        }
        finally {
            LogDiag($"<<< SETUP  EXIT  ctrl={controllerIndex} returnedId={(returnedId ?? "(null)")} total={setupTotalSw.ElapsedMilliseconds}ms");
        }
    }

    /// <summary>
    /// Polls for a HID child PDO to appear under the given parent devnode.
    /// Replaces fixed Thread.Sleep calls with an event-driven poll that exits
    /// as soon as the condition is met. On fast machines this returns in
    /// &lt;100ms; on slow machines it adapts up to the timeout.
    /// </summary>
    private static bool WaitForHidChild(string parentInstanceId, int timeoutMs)
    {
        // v1.3.0 wip 12 — kept the registry-poll shape after empirical
        // diag-log evidence that DeviceManager.WaitForHidChild's
        // CM_Register_Notification path returns False immediately for SWD-
        // rooted gamepad-companion parents (the registration call itself
        // errors before xinputhid has had time to bind the HID child PDO).
        // Polling is robust to that race; the 25 ms tighter cadence vs
        // the v1.2.x 100 ms cadence is the actual win here (75 ms tail
        // wait saved per controller after the child appears).
        int budget = TimeoutScale.Apply(timeoutMs);
        var sw = Stopwatch.StartNew();
        while (sw.ElapsedMilliseconds < budget)
        {
            if (DeviceManager.GetHidChildId(parentInstanceId) != null)
                return true;
            Thread.Sleep(25);
        }
        return false;
    }

    /// <summary>
    /// Polls for a device to reach DN_STARTED status (driver fully bound and
    /// device functional). Replaces fixed Thread.Sleep calls that waited for
    /// xinputhid or other upper filters to finish binding.
    /// </summary>
    private static bool WaitForDeviceStarted(string instanceId, int timeoutMs)
    {
        // CM_Register_Notification doesn't signal on DN_STARTED transitions
        // (PnP only surfaces add/remove/interface events), so this stays as
        // a poll loop. v1.3.0 — tighter poll cadence (25 ms vs 100 ms)
        // reduces the worst-case tail wait from ~99 ms to ~24 ms after
        // the device actually starts. The 75 ms saved on every Xbox-family
        // create adds up across multi-controller batches.
        int budget = TimeoutScale.Apply(timeoutMs);
        var sw = Stopwatch.StartNew();
        while (sw.ElapsedMilliseconds < budget)
        {
            if (CM_Locate_DevNodeW(out uint devInst, instanceId, 0) == 0)
            {
                if (CM_Get_DevNode_Status(out uint status, out _, devInst, 0) == 0
                    && (status & DN_STARTED) != 0)
                    return true;
            }
            Thread.Sleep(25);
        }
        return false;
    }

    /// <summary>Count XInput slots currently reporting connected. Used by
    /// SetupController to wait for the slot claim after each Xbox controller
    /// is created so multi-Xbox setups get deterministic slot ordering.</summary>
    private static int CountConnectedXInputSlots()
    {
        int count = 0;
        for (uint slot = 0; slot < 4; slot++)
            if (XInputGetState(slot, out _) == 0) count++;
        return count;
    }

    [System.Runtime.InteropServices.DllImport("xinput1_4.dll")]
    private static extern uint XInputGetState(uint dwUserIndex, out _XINPUT_STATE pState);

    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    private struct _XINPUT_GAMEPAD
    {
        public ushort wButtons; public byte bLeftTrigger; public byte bRightTrigger;
        public short sThumbLX; public short sThumbLY; public short sThumbRX; public short sThumbRY;
    }

    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    private struct _XINPUT_STATE
    {
        public uint dwPacketNumber; public _XINPUT_GAMEPAD Gamepad;
    }

    // ════════════════════════════════════════════════════════════════════
    //  TeardownController
    // ════════════════════════════════════════════════════════════════════

    public static void TeardownController(int controllerIndex, string? instanceId)
        => TeardownController(controllerIndex, instanceId, skipOrphanSweep: false);

    /// <summary>Internal overload used by HMContext.Dispose's parallel batch
    /// teardown to skip the system-wide HID orphan sweep on every controller.
    /// The sweep is idempotent but expensive — running it once at the end of
    /// the batch (instead of N times concurrently) is a clean win.</summary>
    internal static void TeardownController(int controllerIndex, string? instanceId, bool skipOrphanSweep)
    {
        BeginTeardownGate(controllerIndex);
        try
        {
            foreach (var owned in VaporLockIntegration.OwnedForIndex(controllerIndex))
            {
                VaporLockIntegration.RequireOwned(owned);
                // Only exact returned instances from this process; never name/index sweeps.
                if (!DeviceManager.RemoveDevice(owned, timeoutMs: 15000, forceFallbacks: false))
                    throw new InvalidOperationException("An owned controller remains pending removal.");
                VaporLockIntegration.RecordRemoved(owned);
            }
            SharedMemoryIO.DestroyController(controllerIndex);
            VaporLockIntegration.RemoveOwnedInstanceConfiguration(controllerIndex);
        }
        finally { EndTeardownGate(controllerIndex); }
    }

    /// <summary>One-time orphan sweep used after a batch teardown
    /// (HMContext.Dispose with multiple controllers). Skipping the per-
    /// controller sweep + running this once is what avoids the redundant
    /// system-wide HID enumeration.</summary>
    internal static void RemoveOrphanHidChildrenBatch()
    {
        // Shared vendor sweeps/service mutation are disabled.
    }

    // ════════════════════════════════════════════════════════════════════
    //  RemoveAllVirtualControllers — purge every HIDMaestro virtual device
    //  from the system, including orphans from prior runs. Used by the
    //  "cleanup" CLI command and by consumers who want a clean slate.
    // ════════════════════════════════════════════════════════════════════

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr OpenEventW(uint dwDesiredAccess, bool bInheritHandle, string lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetEvent(IntPtr hEvent);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr hObject);

    private const uint EVENT_MODIFY_STATE = 0x0002;

    /// <summary>
    /// Signal the named HIDMaestro StopEvents for the given controller
    /// indices, then sleep 500ms to give blocked WUDFHost worker threads
    /// a chance to exit. Without this, after a force-kill the workers
    /// stay blocked on WaitForMultipleObjects(StopEvent, InputDataEvent)
    /// and PnP removal blocks ~1s/device waiting for the kernel
    /// query-remove to time out. With the signal: workers exit, WUDFHost
    /// releases, subsequent DIF_REMOVE completes in milliseconds. For
    /// graceful shutdown the signal makes cleanup near-instant.
    ///
    /// <para>Issue #38: <paramref name="targets"/> is the index set
    /// harvested from the devnodes the sweep is actually about to remove
    /// (<see cref="CollectSweepTargetIndices"/>), not an unconditional
    /// 0..15 broadcast. The named events are shared machine-wide, so the
    /// old broadcast signaled every live process's workers on every
    /// sweep. The driver's TearingDown gate makes a stray signal
    /// non-lethal now (workers recycle instead of exiting), but the
    /// sweep still should not make other sessions' workers churn. Pass
    /// null to fall back to the broadcast set (0..15 plus every
    /// Controller&lt;N&gt; config key), used when a devnode was found
    /// whose index could not be read.</para>
    /// </summary>
    private static void SignalStopEventsAndDrain(HashSet<int>? targets)
    {
        HashSet<int> indices;
        if (targets != null)
        {
            indices = targets;
        }
        else
        {
            // Broadcast fallback: 0-15 baseline plus any Controller<N>
            // config key (the 2026-07-21 perf audit: indexing is
            // unbounded, so controllers >= 16 previously missed the
            // fast-stop signal and paid the slow query-remove path).
            indices = new HashSet<int>(Enumerable.Range(0, 16));
            try
            {
                using var hmKey = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\HIDMaestro");
                if (hmKey != null)
                    foreach (var name in hmKey.GetSubKeyNames())
                        if (name.StartsWith("Controller", StringComparison.OrdinalIgnoreCase)
                            && int.TryParse(name.Substring(10), out int idx))
                            indices.Add(idx);
            }
            catch { }
        }

        bool anySignaled = false;
        foreach (int i in indices)
        {
            IntPtr ev = OpenEventW(EVENT_MODIFY_STATE, false, $@"Global\HIDMaestroStopEvent{i}");
            if (ev != IntPtr.Zero)
            {
                SetEvent(ev);
                CloseHandle(ev);
                anySignaled = true;
            }
        }
        // Drain only when a live driver worker was actually signaled. The
        // unconditional 500 ms sleep ran on EVERY launch (twice on the
        // documented InstallDriver + explicit-cleanup sequence) even with
        // zero orphans on a clean machine.
        if (anySignaled)
            Thread.Sleep(TimeoutScale.Apply(500));
    }

    /// <summary>Read-only pre-pass over the exact enumerator/ownership set
    /// the sweep below removes, harvesting each owned devnode's
    /// <c>Device Parameters\ControllerIndex</c>. Returns null when any
    /// owned devnode's index could not be read, which tells the caller to
    /// fall back to the broadcast signal so a force-killed session's
    /// worker is never left blocking PnP removal (issue #38).</summary>
    private static HashSet<int>? CollectSweepTargetIndices()
    {
        var indices = new HashSet<int>();
        bool unindexed = false;
        foreach (var enumRoot in new[] { "ROOT", "SWD" })
        try
        {
            using var enumKey = Registry.LocalMachine.OpenSubKey($@"SYSTEM\CurrentControlSet\Enum\{enumRoot}");
            if (enumKey == null) continue;
            foreach (var sub in enumKey.GetSubKeyNames())
            {
                bool exclusivelyOurs = sub.StartsWith("HIDMAESTRO", StringComparison.OrdinalIgnoreCase);
                bool shared = sub.StartsWith("VID_", StringComparison.OrdinalIgnoreCase)
                    || sub.Equals("XnaComposite", StringComparison.OrdinalIgnoreCase)
                    || sub.Equals("HID_IG_00", StringComparison.OrdinalIgnoreCase)
                    || sub.Equals("HIDCLASS", StringComparison.OrdinalIgnoreCase)
                    || sub.Equals("SYSTEM", StringComparison.OrdinalIgnoreCase);
                if (!exclusivelyOurs && !shared) continue;

                using var subKey = enumKey.OpenSubKey(sub);
                if (subKey == null) continue;
                foreach (var inst in subKey.GetSubKeyNames())
                {
                    if (!exclusivelyOurs
                        && !DeviceManager.IsHidMaestroOwned($@"{enumRoot}\{sub}\{inst}"))
                        continue;
                    using var dp = subKey.OpenSubKey($@"{inst}\Device Parameters");
                    if (dp?.GetValue("ControllerIndex") is int ci)
                        indices.Add(ci);
                    else
                        unindexed = true;
                }
            }
        }
        catch { unindexed = true; }
        return unindexed ? null : indices;
    }

    public static void RemoveAllVirtualControllers()
        => RemoveAllVirtualControllers(preserveInstall: false);

    /// <summary>Core sweep. <paramref name="preserveInstall"/> = true is the
    /// launch path (HMContext.InstallDriver): evict every device and orphan
    /// exactly as the full sweep does (the documented prerequisite for a
    /// driver UPGRADE's package replacement to succeed) but keep the
    /// installed driver packages and the manifest-hash fast-path intact.
    /// The 2026-07-21 perf audit found the launch path deleting both on
    /// every start, which forced FullDeploy's full extract+sign+catalog+
    /// pnputil pipeline (~3.0-3.2 s measured) on EVERY consumer launch and
    /// re-introduced package delete/add churn (the FAILED_PRIOR_UNLOAD
    /// class) for zero benefit in the same-version case. When the embedded
    /// payload actually differs, FullDeploy's full path still runs and its
    /// RemoveOldDriverPackages purges the old packages, with devices
    /// already evicted here so the ordering contract holds. The standalone
    /// cleanup command keeps preserveInstall=false: full nuke stays its
    /// documented job.</summary>
    internal static void RemoveAllVirtualControllers(bool preserveInstall)
    {
        throw new NotSupportedException("Global vendor removal is disabled.");
    }

    /// <summary>Terminate WUDFHost instances that are hosting ONLY our
    /// HIDMaestro driver (plus UMDF framework DLLs). Skips any WUDFHost
    /// that has another third-party driver loaded — killing those would
    /// break real devices (most notably real Bluetooth Xbox controllers
    /// hosted by microsoft.bluetooth.profiles.hidovergatt.dll).
    ///
    /// Why this is needed: pnputil /delete-driver removes the DLL from
    /// disk but doesn't unload it from running WUDFHost processes. Those
    /// keep the OLD code mapped until the process itself exits. Fresh
    /// device creation after an INF upgrade can bind a new device into
    /// an existing WUDFHost that has the stale mapping — so the new
    /// device runs old code. Killing the safe-to-terminate instances
    /// lets PnP spawn a fresh WUDFHost that loads the fresh DLL from
    /// the current DriverStore directory.</summary>
    private static void DrainOrphanedWudfHosts()
    {
        // Framework / OS modules that every WUDFHost loads by default.
        // Presence of anything OUTSIDE this set (other than our own
        // HIDMaestro.dll / HMXInput.dll) marks the WUDFHost as hosting
        // a third-party driver we must not disrupt.
        var frameworkModules = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var name in new[] {
            "WUDFHost.exe", "WUDFPlatform.dll", "WUDFx02000.dll",
            "WUDFCoinstaller.dll", "WudfSMCClassExt.dll", "Mshidumdf.dll",
        }) frameworkModules.Add(name);

        // Our own UMDF drivers — fine to terminate a host that runs only these.
        var ourDrivers = new HashSet<string>(StringComparer.OrdinalIgnoreCase) {
            "HIDMaestro.dll", "HMXInput.dll", "HIDMaestroCompanion.dll",
        };

        foreach (var proc in System.Diagnostics.Process.GetProcessesByName("WUDFHost"))
        {
            try
            {
                bool hostsThirdParty = false;
                bool hostsOurs = false;

                foreach (System.Diagnostics.ProcessModule m in proc.Modules)
                {
                    string name = m.ModuleName ?? "";
                    string path = m.FileName ?? "";

                    // Framework — ignore
                    if (frameworkModules.Contains(name) ||
                        path.StartsWith(@"C:\Windows\System32\", StringComparison.OrdinalIgnoreCase) &&
                        !path.Contains(@"\DriverStore\", StringComparison.OrdinalIgnoreCase) &&
                        !path.Contains(@"\drivers\umdf\", StringComparison.OrdinalIgnoreCase))
                        continue;

                    // Ours — note it but keep scanning (a WUDFHost with BOTH
                    // ours AND third-party is still must-not-kill).
                    if (ourDrivers.Contains(name)) { hostsOurs = true; continue; }

                    // Anything else loaded from DriverStore or the UMDF drivers
                    // dir — this is another third-party UMDF driver. Abort.
                    if (path.Contains(@"\DriverStore\FileRepository\", StringComparison.OrdinalIgnoreCase) ||
                        path.Contains(@"\System32\drivers\umdf\", StringComparison.OrdinalIgnoreCase))
                    {
                        hostsThirdParty = true;
                        break;
                    }
                }

                if (hostsOurs && !hostsThirdParty)
                {
                    try { proc.Kill(); proc.WaitForExit(TimeoutScale.Apply(2000)); } catch { }
                }
            }
            catch { /* access denied, process exited, etc. — skip this host */ }
            finally { proc.Dispose(); }
        }
    }

    // ════════════════════════════════════════════════════════════════════
    //  ComputeInputReportByteLength
    // ════════════════════════════════════════════════════════════════════

    /// <summary>Compute the input report byte length from a HID descriptor.
    /// Ported verbatim from the proven pre-SDK test app's implementation.
    /// Critically: handles multi-Report-ID descriptors (e.g. dualsense which
    /// declares Report IDs 1, 2, 3, ...) by counting bits ONLY for the first
    /// encountered Report ID — that's the input report we use. Adds +1 to
    /// the byte total for descriptors that have any Report ID, accounting
    /// for the prefix byte the kernel HID stack adds when delivering reports.
    /// </summary>
    private static int ComputeInputReportByteLength(byte[] desc)
    {
        int totalBits = 0;
        int reportSize = 0;
        int reportCount = 0;
        int currentReportId = 0;
        int firstInputReportId = 0;
        bool hasReportIds = false;

        for (int i = 0; i < desc.Length;)
        {
            byte prefix = desc[i];
            int bSize = prefix & 0x03;
            if (bSize == 3) bSize = 4;
            int bType = (prefix >> 2) & 0x03;
            int bTag = (prefix >> 4) & 0x0F;

            int value = 0;
            if (i + bSize < desc.Length)
            {
                for (int j = 0; j < bSize; j++)
                    value |= desc[i + 1 + j] << (8 * j);
            }

            if (bType == 1) // Global
            {
                if (bTag == 7) reportSize = value;     // Report Size
                if (bTag == 9) reportCount = value;    // Report Count
                if (bTag == 8)                          // Report ID
                {
                    currentReportId = value;
                    if (!hasReportIds) firstInputReportId = value;
                    hasReportIds = true;
                }
            }
            else if (bType == 0) // Main
            {
                if (bTag == 8) // Input
                {
                    // Count bits for the first report ID encountered (or all
                    // bits if the descriptor doesn't use Report IDs at all).
                    if (!hasReportIds || currentReportId == firstInputReportId)
                        totalBits += reportSize * reportCount;
                }
            }

            i += 1 + bSize;
        }

        int totalBytes = (totalBits + 7) / 8;
        return hasReportIds ? totalBytes + 1 : totalBytes; // +1 for Report ID byte
    }
}
