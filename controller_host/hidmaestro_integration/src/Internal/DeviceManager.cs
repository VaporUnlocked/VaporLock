using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Win32;

namespace HIDMaestro.Internal;

/// <summary>
/// Event-driven PnP device management using CM_Register_Notification.
/// No sleeps, no polling — waits on OS callbacks for actual system state changes.
/// </summary>
public static class DeviceManager
{
    // ── CfgMgr32 P/Invoke ──

    [DllImport("CfgMgr32.dll", CharSet = CharSet.Unicode)]
    static extern uint CM_Locate_DevNodeW(out uint pdnDevInst, string pDeviceID, uint ulFlags);

    [DllImport("CfgMgr32.dll")]
    static extern uint CM_Get_Child(out uint pdnDevInst, uint dnDevInst, uint ulFlags);

    [DllImport("CfgMgr32.dll")]
    static extern uint CM_Get_Sibling(out uint pdnDevInst, uint dnDevInst, uint ulFlags);

    [DllImport("CfgMgr32.dll")]
    static extern uint CM_Get_Parent(out uint pdnDevInst, uint dnDevInst, uint ulFlags);

    [DllImport("CfgMgr32.dll")]
    static extern uint CM_Uninstall_DevNode(uint dnDevInst, uint ulFlags);

    [DllImport("CfgMgr32.dll", CharSet = CharSet.Unicode)]
    static extern uint CM_Register_Notification(ref CM_NOTIFY_FILTER pFilter, IntPtr pContext, CM_NOTIFY_CALLBACK pCallback, out IntPtr pNotifyContext);

    [DllImport("CfgMgr32.dll")]
    static extern uint CM_Unregister_Notification(IntPtr NotifyContext);

    [DllImport("CfgMgr32.dll")]
    static extern uint CM_Get_DevNode_Status(out uint pulStatus, out uint pulProblemNumber, uint dnDevInst, uint ulFlags);

    [DllImport("CfgMgr32.dll", CharSet = CharSet.Unicode)]
    static extern uint CM_Get_Device_ID_Size(out uint pulLen, uint dnDevInst, uint ulFlags);

    [DllImport("CfgMgr32.dll", CharSet = CharSet.Unicode)]
    static extern uint CM_Get_Device_IDW(uint dnDevInst, char[] Buffer, uint BufferLen, uint ulFlags);

    delegate uint CM_NOTIFY_CALLBACK(IntPtr hNotify, IntPtr Context, uint Action, ref CM_NOTIFY_EVENT_DATA EventData, uint EventDataSize);

    const uint CM_NOTIFY_FILTER_TYPE_DEVICEINTERFACE = 0;
    const uint CM_NOTIFY_FILTER_TYPE_DEVICEINSTANCE = 2;

    const uint CM_NOTIFY_ACTION_DEVICEINTERFACEARRIVAL = 0;
    const uint CM_NOTIFY_ACTION_DEVICEINSTANCEREMOVED = 9;

    const uint CR_SUCCESS = 0;
    const uint DN_STARTED = 0x00000008;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct CM_NOTIFY_FILTER
    {
        public uint cbSize;
        public uint Flags;
        public uint FilterType;
        public uint Reserved;
        // Union: for DEVICEINTERFACE, this is the ClassGuid
        public Guid ClassGuid;
        // Padding for the union (device instance string or handle)
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 200)]
        public string? FilterData;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct CM_NOTIFY_EVENT_DATA
    {
        public uint FilterType;
        public uint Reserved;
        // Union depends on FilterType
        public Guid ClassGuid;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 200)]
        public string? SymbolicLink;
    }

    static readonly Guid HID_INTERFACE_GUID = new("4D1E55B2-F16F-11CF-88CB-001111000030");

    /// <summary>
    /// Waits for a specific device interface GUID to appear on the given device.
    /// Uses CM_Register_Notification — no polling, no sleeping.
    /// </summary>
    public static bool WaitForDeviceInterface(string instanceId, Guid interfaceGuid, int timeoutMs = 10000)
    {
        using var readyEvent = new ManualResetEventSlim(false);

        // Check if already present
        // (CM_Locate + check interfaces via registry)
        if (HasInterface(instanceId, interfaceGuid))
            return true;

        var filter = new CM_NOTIFY_FILTER
        {
            cbSize = (uint)Marshal.SizeOf<CM_NOTIFY_FILTER>(),
            FilterType = CM_NOTIFY_FILTER_TYPE_DEVICEINTERFACE,
            ClassGuid = interfaceGuid
        };

        string instHash = instanceId.Replace('\\', '#').ToLowerInvariant();

        CM_NOTIFY_CALLBACK callback = (IntPtr hNotify, IntPtr context, uint action, ref CM_NOTIFY_EVENT_DATA data, uint size) =>
        {
            if (action == CM_NOTIFY_ACTION_DEVICEINTERFACEARRIVAL)
            {
                string? symLink = data.SymbolicLink;
                if (symLink != null && symLink.ToLowerInvariant().Contains(instHash))
                    readyEvent.Set();
            }
            return 0u;
        };

        var callbackHandle = GCHandle.Alloc(callback);
        try
        {
            if (CM_Register_Notification(ref filter, IntPtr.Zero, callback, out IntPtr notifyCtx) != CR_SUCCESS)
                return false;

            try
            {
                // Re-check after registration (race window)
                if (HasInterface(instanceId, interfaceGuid))
                    return true;
                return readyEvent.Wait(TimeoutScale.Apply(timeoutMs));
            }
            finally
            {
                CM_Unregister_Notification(notifyCtx);
            }
        }
        finally
        {
            callbackHandle.Free();
        }
    }

    static bool HasInterface(string instanceId, Guid interfaceGuid)
    {
        // Quick check via registry
        string regPath = $@"SYSTEM\CurrentControlSet\Control\DeviceClasses\{{{interfaceGuid}}}";
        try
        {
            using var key = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(regPath);
            if (key == null) return false;
            string hash = instanceId.Replace('\\', '#').ToLowerInvariant();
            return key.GetSubKeyNames().Any(s => s.ToLowerInvariant().Contains(hash));
        }
        catch { return false; }
    }

    /// <summary>
    /// Waits for a HID device interface to appear for the given device instance ID.
    /// Returns true when the HID child is ready, false on timeout.
    /// </summary>
    public static bool WaitForHidChild(string parentInstanceId, int timeoutMs = 5000)
    {
        using var readyEvent = new ManualResetEventSlim(false);

        // Check if already present
        if (CM_Locate_DevNodeW(out uint parentInst, parentInstanceId, 0) == CR_SUCCESS)
        {
            if (CM_Get_Child(out uint _, parentInst, 0) == CR_SUCCESS)
                return true; // Already has a child
        }

        // Register for HID interface arrival
        var filter = new CM_NOTIFY_FILTER
        {
            cbSize = (uint)Marshal.SizeOf<CM_NOTIFY_FILTER>(),
            FilterType = CM_NOTIFY_FILTER_TYPE_DEVICEINTERFACE,
            ClassGuid = HID_INTERFACE_GUID
        };

        CM_NOTIFY_CALLBACK callback = (IntPtr hNotify, IntPtr context, uint action, ref CM_NOTIFY_EVENT_DATA data, uint size) =>
        {
            if (action == CM_NOTIFY_ACTION_DEVICEINTERFACEARRIVAL)
            {
                string? symLink = data.SymbolicLink;
                if (symLink != null)
                {
                    string parentHash = parentInstanceId.Replace('\\', '#').ToLowerInvariant();
                    if (symLink.ToLowerInvariant().Contains(parentHash))
                        readyEvent.Set();
                }
            }
            return 0u;
        };

        var callbackHandle = GCHandle.Alloc(callback);
        try
        {
            if (CM_Register_Notification(ref filter, IntPtr.Zero, callback, out IntPtr notifyCtx) != CR_SUCCESS)
                return false;

            try
            {
                // Check again after registration (race condition window)
                uint childInst;
                if (CM_Locate_DevNodeW(out parentInst, parentInstanceId, 0) == CR_SUCCESS
                    && CM_Get_Child(out childInst, parentInst, 0) == CR_SUCCESS)
                    return true;

                return readyEvent.Wait(TimeoutScale.Apply(timeoutMs));
            }
            finally
            {
                CM_Unregister_Notification(notifyCtx);
            }
        }
        finally
        {
            callbackHandle.Free();
        }
    }

    /// <summary>
    /// Waits for a device instance to be fully removed.
    /// Returns true when gone, false on timeout.
    /// </summary>
    public static bool WaitForDeviceRemoval(string instanceId, int timeoutMs = 5000)
    {
        // Check if already gone
        if (CM_Locate_DevNodeW(out uint _, instanceId, 0) != CR_SUCCESS)
            return true;

        using var goneEvent = new ManualResetEventSlim(false);

        var filter = new CM_NOTIFY_FILTER
        {
            cbSize = (uint)Marshal.SizeOf<CM_NOTIFY_FILTER>(),
            FilterType = CM_NOTIFY_FILTER_TYPE_DEVICEINSTANCE,
            FilterData = instanceId
        };

        CM_NOTIFY_CALLBACK callback = (IntPtr hNotify, IntPtr context, uint action, ref CM_NOTIFY_EVENT_DATA data, uint size) =>
        {
            if (action == CM_NOTIFY_ACTION_DEVICEINSTANCEREMOVED)
                goneEvent.Set();
            return 0u;
        };

        var callbackHandle = GCHandle.Alloc(callback);
        try
        {
            if (CM_Register_Notification(ref filter, IntPtr.Zero, callback, out IntPtr notifyCtx) != CR_SUCCESS)
                return false;

            try
            {
                // Check again after registration
                uint devInst2;
                if (CM_Locate_DevNodeW(out devInst2, instanceId, 0) != CR_SUCCESS)
                    return true;

                return goneEvent.Wait(TimeoutScale.Apply(timeoutMs));
            }
            finally
            {
                CM_Unregister_Notification(notifyCtx);
            }
        }
        finally
        {
            callbackHandle.Free();
        }
    }

    // ── SetupAPI P/Invoke for DIF_REMOVE ──

    [DllImport("SetupAPI.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "SetupDiGetClassDevsW")]
    static extern IntPtr SetupDiGetClassDevsByRef(ref Guid ClassGuid, uint Enumerator,
        IntPtr hwndParent, uint Flags);

    [DllImport("SetupAPI.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool SetupDiOpenDeviceInfoW(IntPtr DeviceInfoSet, string DeviceInstanceId,
        IntPtr hwndParent, uint OpenFlags, IntPtr DeviceInfoData);

    [DllImport("SetupAPI.dll", SetLastError = true, EntryPoint = "SetupDiCallClassInstaller")]
    static extern bool SetupDiCallClassInstaller(int InstallFunction, IntPtr DeviceInfoSet, IntPtr DeviceInfoData);

    [DllImport("SetupAPI.dll", SetLastError = true)]
    static extern bool SetupDiDestroyDeviceInfoList(IntPtr DeviceInfoSet);

    [DllImport("SetupAPI.dll", SetLastError = true)]
    static extern bool SetupDiEnumDeviceInfo(IntPtr DeviceInfoSet, uint MemberIndex, IntPtr DeviceInfoData);

    [DllImport("SetupAPI.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool SetupDiGetDeviceInstanceIdW(IntPtr DeviceInfoSet, IntPtr DeviceInfoData,
        [Out] char[] DeviceInstanceId, uint DeviceInstanceIdSize, out uint RequiredSize);

    [DllImport("SetupAPI.dll", SetLastError = true)]
    static extern bool SetupDiRemoveDevice(IntPtr DeviceInfoSet, IntPtr DeviceInfoData);

    const uint DIGCF_ALLCLASSES = 0x04;
    const int DIF_REMOVE = 0x05;

    /// <summary>
    /// Sweep accumulated HIDMaestro phantom devnodes from the Windows PnP
    /// registry cache. v1.1.30's per-call unique SwD instance suffix
    /// guarantees a fresh devnode every create, but Windows preserves a
    /// per-instance registry hive entry indefinitely after teardown.
    /// Across many sessions these accumulate as "hidden devices" in
    /// Device Manager. They are functionally harmless (no XInput slot,
    /// no joy.cpl/WGI/RawInput/SDL3 visibility, only surfaced by
    /// "Show Hidden Devices") but visually clutter Device Manager for
    /// power users running many sessions per boot.
    ///
    /// SetupDiRemoveDevice via DIGCF_ALLCLASSES enumerator is the
    /// documented path that actually deletes the registry hive cache
    /// for non-present devnodes (verified empirically 2026-04-25:
    /// 28/28 phantoms removed in 492ms, zero failures, PRESENT
    /// devnodes correctly skipped).
    ///
    /// Skips PRESENT devnodes — only touches PHANTOM/non-loaded ones.
    /// Safe to call while a HIDMaestro session is live; will not disturb
    /// any current controller. Returns the number of phantoms removed.
    /// </summary>
    internal static int RemoveAccumulatedHmPhantoms()
    {
        return 0; // no shared namespace sweeps
    }

    /// <summary>
    /// Returns true when the PnP devnode at <paramref name="instanceId"/> carries
    /// a HIDMaestro-signature HardwareID — i.e. one of `root\HIDMaestro`,
    /// `root\HIDMaestroGamepad`, `root\HIDMaestroXUSB` (or any future
    /// `*HIDMaestro*` literal). Every HM-created devnode writes one of those
    /// strings into its HardwareID multi-sz at SetupDi-create time (plain HID
    /// at DeviceNodeCreator.cs:121, gamepad companion at
    /// DeviceOrchestrator.cs:889-894, XUSB at DeviceOrchestrator.cs:1019-1020),
    /// so the presence of "HIDMaestro" in HardwareID is the ownership proof
    /// every sweep needs before disabling / uninstalling / renaming /
    /// property-writing a node.
    ///
    /// <para>Issue #28 (v1.3.16): without this guard the
    /// <c>CleanupGhostDevices</c> / <c>SetBusTypeGuidUsb</c> /
    /// <c>RemoveAllVirtualControllers</c> / <c>DisableGhostXusbInterfaces</c>
    /// sweeps selected by instance-path pattern alone and ran their
    /// destructive operations against whatever matched — disabling a
    /// coexisting vJoy root HIDClass device, mutating ViGEmBus / HidHide
    /// SYSTEM nodes, or removing third-party root-enumerated VID_ devices
    /// the user never intended HIDMaestro to touch.</para>
    ///
    /// <para>Returns false on missing devnode, missing HardwareID value,
    /// registry-access exception, or no "HIDMaestro" substring match.
    /// Conservative-by-default: when in doubt, do NOT claim ownership.</para>
    /// </summary>
    internal static bool IsHidMaestroOwned(string instanceId)
    {
        if (string.IsNullOrEmpty(instanceId)) return false;
        try
        {
            using var k = Registry.LocalMachine.OpenSubKey(
                $@"SYSTEM\CurrentControlSet\Enum\{instanceId}");
            if (k == null) return false;
            var hwIds = k.GetValue("HardwareID") as string[];
            if (hwIds == null) return false;
            return Array.Exists(hwIds, id => id != null &&
                id.IndexOf("HIDMaestro", StringComparison.OrdinalIgnoreCase) >= 0);
        }
        catch { return false; }
    }

    /// <summary>
    /// Removes a device and all its children, waits for removal to complete.
    /// Enumerates HID children first and removes them individually via DIF_REMOVE,
    /// then removes the parent. This prevents ghost HID children from surviving.
    /// </summary>
    public static bool RemoveDevice(string instanceId, int timeoutMs = 5000, bool fast = false, bool forceFallbacks = false)
    {
        VaporLockIntegration.RequireOwned(instanceId);
        return VaporLockIntegration.RemoveOwnedDeviceExact(instanceId);
    }

    /// <summary>Locate devcon.exe under the installed WDK. Returns the first
    /// match under C:\Program Files (x86)\Windows Kits\10\Tools\*\x64\, or
    /// null if no WDK Tools directory is found. Result is cached for the
    /// lifetime of the process — a missing devcon won't re-probe on every
    /// fallback invocation.</summary>
    private static string? s_devconPath;
    private static bool s_devconProbed;
    private static string? TryLocateDevcon()
    {
        if (s_devconProbed) return s_devconPath;
        s_devconProbed = true;
        try
        {
            string root = @"C:\Program Files (x86)\Windows Kits\10\Tools";
            if (!System.IO.Directory.Exists(root)) return null;
            foreach (var dir in System.IO.Directory.EnumerateDirectories(root))
            {
                string candidate = System.IO.Path.Combine(dir, "x64", "devcon.exe");
                if (System.IO.File.Exists(candidate))
                {
                    s_devconPath = candidate;
                    return candidate;
                }
            }
        }
        catch { }
        return null;
    }

    /// <summary>
    /// Gets all child device instance IDs for a given devnode (CM_Get_Child + CM_Get_Sibling loop).
    /// </summary>
    static List<string> GetAllChildDeviceIds(uint parentInst)
    {
        var children = new List<string>();
        if (CM_Get_Child(out uint childInst, parentInst, 0) != CR_SUCCESS)
            return children;

        do
        {
            string? childId = GetDeviceId(childInst);
            if (childId != null)
                children.Add(childId);
        }
        while (CM_Get_Sibling(out childInst, childInst, 0) == CR_SUCCESS);

        return children;
    }

    /// <summary>
    /// Gets the device instance ID string for a given devnode. T33-2 — uses
    /// the same thread-local reusable buffer as GetHidChildId so per-call
    /// char[] allocations on enumeration paths (GetAllChildDeviceIds,
    /// RemoveOrphanHidChildren) don't generate GC pressure on slow hardware.
    /// </summary>
    static string? GetDeviceId(uint devInst)
    {
        if (CM_Get_Device_ID_Size(out uint idLen, devInst, 0) != CR_SUCCESS)
            return null;
        var buffer = t_devIdBuffer;
        if (buffer == null || buffer.Length < idLen + 1)
            buffer = t_devIdBuffer = new char[Math.Max(512, (int)idLen + 1)];
        if (CM_Get_Device_IDW(devInst, buffer, (uint)buffer.Length, 0) != CR_SUCCESS)
            return null;
        return new string(buffer, 0, (int)idLen);
    }

    /// <summary>
    /// Removes a single device via SetupDiRemoveDevice (same as devcon remove).
    /// This is more thorough than DIF_REMOVE — it bypasses class installers and
    /// directly removes the device node, preventing ghost entries on UMDF devices.
    /// Falls back to CM_Disable + CM_Uninstall if SetupAPI fails.
    /// </summary>
    static void DifRemoveDevice(string instanceId)
    {
        uint devInst = 0;
        bool located = CM_Locate_DevNodeW(out devInst, instanceId, 0) == CR_SUCCESS
                    || CM_Locate_DevNodeW(out devInst, instanceId, 1) == CR_SUCCESS;

        // Use Guid.Empty + DIGCF_ALLCLASSES to enumerate ALL devices including phantoms.
        // This is critical — a device-class-specific DevInfoSet won't find phantoms.
        Guid nullGuid = Guid.Empty;
        IntPtr dis = SetupDiGetClassDevsByRef(ref nullGuid, 0, IntPtr.Zero, DIGCF_ALLCLASSES);
        if (dis == new IntPtr(-1))
        {
            if (located)
            {
                CM_Disable_DevNode(devInst, 0);
                CM_Uninstall_DevNode(devInst, 0);
            }
            return;
        }

        try
        {
            int devInfoSize = IntPtr.Size == 8 ? 32 : 28;
            byte[] devInfoBuf = new byte[devInfoSize];
            BitConverter.GetBytes(devInfoSize).CopyTo(devInfoBuf, 0);
            var devInfoHandle = GCHandle.Alloc(devInfoBuf, GCHandleType.Pinned);

            try
            {
                if (!SetupDiOpenDeviceInfoW(dis, instanceId, IntPtr.Zero, 0, devInfoHandle.AddrOfPinnedObject()))
                {
                    if (located)
                    {
                        CM_Disable_DevNode(devInst, 0);
                        CM_Uninstall_DevNode(devInst, 0);
                    }
                    return;
                }

                // SetupDiRemoveDevice — direct removal like devcon.exe.
                // Unlike DIF_REMOVE (class installer), this fully removes the device
                // node and registry entries, preventing ghosts on UMDF devices.
                if (!SetupDiRemoveDevice(dis, devInfoHandle.AddrOfPinnedObject()))
                {
                    // Fallback to DIF_REMOVE if direct removal fails
                    SetupDiCallClassInstaller(DIF_REMOVE, dis, devInfoHandle.AddrOfPinnedObject());
                }
            }
            finally
            {
                devInfoHandle.Free();
            }
        }
        finally
        {
            SetupDiDestroyDeviceInfoList(dis);
        }
    }

    [DllImport("CfgMgr32.dll")]
    static extern uint CM_Disable_DevNode(uint dnDevInst, uint ulFlags);

    [DllImport("CfgMgr32.dll")]
    static extern uint CM_Enable_DevNode(uint dnDevInst, uint ulFlags);

    /// <summary>
    /// Restarts a device by disabling and re-enabling it. No process spawning.
    /// </summary>
    public static bool RestartDevice(string instanceId)
    {
        VaporLockIntegration.RequireOwned(instanceId);

        if (CM_Locate_DevNodeW(out uint devInst, instanceId, 0) != CR_SUCCESS)
            return false;
        CM_Disable_DevNode(devInst, 0);
        // Wait for driver to unload before re-enabling. The 5 s base is
        // a backstop — WaitForDeviceRemoval is signal-driven internally
        // via CM_Register_Notification, this just caps the wait.
        // WaitForDeviceRemoval applies TimeoutScale internally.
        WaitForDeviceRemoval(instanceId, 5000);
        // Re-locate (may have become phantom after disable)
        CM_Locate_DevNodeW(out devInst, instanceId, 1); // phantom flag
        CM_Enable_DevNode(devInst, 0);
        return true;
    
    }

    /// <summary>
    /// Returns true if the device (or its HID child) is in DN_STARTED state,
    /// meaning its driver is fully bound and the device is functional.
    /// Used by FinalizeNames to poll for PnP readiness instead of fixed sleeps.
    /// </summary>
    public static bool IsDeviceStarted(string instanceId)
    {
        // Check the device itself first
        if (CM_Locate_DevNodeW(out uint devInst, instanceId, 0) != CR_SUCCESS)
            return false;
        if (CM_Get_DevNode_Status(out uint status, out _, devInst, 0) == CR_SUCCESS
            && (status & DN_STARTED) != 0)
            return true;

        // Also check the HID child (if any) — for virtual controllers the parent
        // may be started but the HID child (where xinputhid binds) is what matters.
        string? hidChild = GetHidChildId(instanceId);
        if (hidChild == null) return false;
        if (CM_Locate_DevNodeW(out uint childInst, hidChild, 0) != CR_SUCCESS)
            return false;
        return CM_Get_DevNode_Status(out uint childStatus, out _, childInst, 0) == CR_SUCCESS
            && (childStatus & DN_STARTED) != 0;
    }

    /// <summary>
    /// Finds the HID child device instance ID for a given parent.
    /// Returns null if no child found.
    /// </summary>
    // T29-2 — thread-local reusable char buffer. WaitForHidChild polls this
    // at 25 ms cadence until the HID child appears; on a cold-start setup
    // that's 4-8 calls per controller, each allocating a fresh char[]. Per-
    // thread reuse eliminates the GC pressure (esp. relevant on Atom-class
    // hardware where every alloc avoided is a cycle saved). Sized at 512
    // chars: covers every realistic device-instance ID length while staying
    // small enough that the per-thread allocation overhead is one-time.
    [ThreadStatic]
    private static char[]? t_devIdBuffer;

    public static string? GetHidChildId(string parentInstanceId)
    {
        if (CM_Locate_DevNodeW(out uint parentInst, parentInstanceId, 0) != CR_SUCCESS)
            return null;

        if (CM_Get_Child(out uint childInst, parentInst, 0) != CR_SUCCESS)
            return null;

        if (CM_Get_Device_ID_Size(out uint idLen, childInst, 0) != CR_SUCCESS)
            return null;

        var buffer = t_devIdBuffer;
        if (buffer == null || buffer.Length < idLen + 1)
            buffer = t_devIdBuffer = new char[Math.Max(512, (int)idLen + 1)];
        if (CM_Get_Device_IDW(childInst, buffer, (uint)buffer.Length, 0) != CR_SUCCESS)
            return null;

        return new string(buffer, 0, (int)idLen);
    }

    /// <summary>
    /// Returns all HID child device instance IDs of the given parent (one per
    /// top-level HID collection the parent advertises). The enumeration must
    /// run BEFORE the parent is torn down — once the parent goes to phantom
    /// state, CM_Get_Child / CM_Get_Sibling on it stop working. Callers that
    /// need to explicitly remove HID children alongside their parent (to
    /// avoid the "half-dead PDO" state where the child survives an async
    /// parent-removal cascade) should call this first, save the list, then
    /// remove parent + each captured child.
    /// Returns an empty list if the parent cannot be located or has no children.
    /// </summary>
    public static List<string> GetAllHidChildIds(string parentInstanceId)
    {
        if (CM_Locate_DevNodeW(out uint parentInst, parentInstanceId, 0) != CR_SUCCESS)
            return new List<string>();
        return GetAllChildDeviceIds(parentInst);
    }

    /// <summary>
    /// Scans HKLM\SYSTEM\CurrentControlSet\Enum\HID for orphaned HM HID
    /// children whose parent (ROOT\ or SWD\) no longer exists. Removes each
    /// via DIF_REMOVE. Returns the number of orphans removed.
    ///
    /// HM ownership is identified by the HardwareIDs of the HID instance —
    /// every HM HID child carries "HID\HIDMaestro" + "HID\HIDMaestroGamepad"
    /// entries from the INF. That's contractual and stable across:
    ///   - the v1.1.20 ROOT\ -> SWD\ enumerator migration
    ///   - VID-spoofing Xbox profiles vs HIDMAESTRO-prefix non-Xbox profiles
    /// — so it correctly catches every flavor of HM child without name-
    /// pattern brittleness.
    /// </summary>
    public static int RemoveOrphanHidChildren()
    {
        return 0; // no shared namespace sweeps
    }

    /// <summary>
    /// Reads the ParentIdPrefix or reconstructs the parent instance ID for a HID child
    /// from the registry.
    /// </summary>
    internal static string? GetRegistryParentId(string enumHidPath, string deviceName, string instanceName)
    {
        try
        {
            string regPath = $@"{enumHidPath}\{deviceName}\{instanceName}";
            using var instKey = Registry.LocalMachine.OpenSubKey(regPath);
            if (instKey == null)
                return null;

            // The HID child stores its parent in the "ParentIdPrefix" or we can
            // derive it from the device ID structure. The HID\VID_XXXX&PID_XXXX&IG_XX\<hash>
            // format encodes the parent info. Check for explicit parent reference first.

            // Try reading the device's actual devnode to get the parent via CM API
            if (CM_Locate_DevNodeW(out uint childInst, $@"HID\{deviceName}\{instanceName}", 0) == CR_SUCCESS
                || CM_Locate_DevNodeW(out childInst, $@"HID\{deviceName}\{instanceName}", 1) == CR_SUCCESS)
            {
                if (CM_Get_Parent(out uint parentInst, childInst, 0) == CR_SUCCESS)
                {
                    string? parentId = GetDeviceId(parentInst);
                    if (parentId != null)
                        return parentId;
                }
            }

            // Fallback: scan ROOT\HIDMAESTRO and ROOT\HIDMAESTROIG entries
            // to see if any match via ParentIdPrefix
            foreach (string rootClass in new[] { "HIDMAESTRO", "HIDMAESTROIG" })
            {
                string rootEnumPath = $@"SYSTEM\CurrentControlSet\Enum\ROOT\{rootClass}";
                using var rootKey = Registry.LocalMachine.OpenSubKey(rootEnumPath);
                if (rootKey == null)
                    continue;

                foreach (string rootInst in rootKey.GetSubKeyNames())
                {
                    string rootInstanceId = $@"ROOT\{rootClass}\{rootInst}";
                    // If the parent no longer exists at all (not even phantom), it's orphaned
                    if (CM_Locate_DevNodeW(out uint _, rootInstanceId, 0) != CR_SUCCESS
                        && CM_Locate_DevNodeW(out uint _, rootInstanceId, 1) != CR_SUCCESS)
                    {
                        // Check if this root device's ParentIdPrefix matches the HID child's instance
                        using var rootInstKey = rootKey.OpenSubKey(rootInst);
                        string? pip = rootInstKey?.GetValue("ParentIdPrefix") as string;
                        if (pip != null && instanceName.ToUpperInvariant().Contains(pip.ToUpperInvariant()))
                            return rootInstanceId;
                    }
                }
            }
        }
        catch { }

        return null;
    }
}
