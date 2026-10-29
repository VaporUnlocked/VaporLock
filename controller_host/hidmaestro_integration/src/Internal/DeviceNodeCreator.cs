using System;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32;

namespace HIDMaestro.Internal;

/// <summary>
/// Per-controller virtual device node creation. This is the heart of HIDMaestro
/// — it builds a root-enumerated PnP device of the right HID class with the
/// right hardware ID, writes the per-instance ControllerIndex, installs the
/// driver against it, and waits for the HID class to layer on top before
/// returning.
///
/// <para>Three enumerator paths exist, picked from the profile:</para>
/// <list type="bullet">
/// <item><b>xinputhid path</b> (Xbox Series controllers): hardware ID has
/// <c>&amp;IG_00</c> + <c>DriverPid</c> override that matches xinputhid's INF.
/// xinputhid auto-binds and presents the device as XInput.</item>
/// <item><b>Xbox legacy path</b> (Xbox 360 wired etc): hardware ID has
/// <c>&amp;IG_00</c> for HIDAPI/SDL3 filtering plus <c>USB\MS_COMP_XUSB10</c>
/// compatible IDs so WGI recognizes it as a gamepad. The XUSB companion
/// is created separately.</item>
/// <item><b>Plain HID path</b> (DualSense, generic gamepads): standard
/// HIDClass enumerator. No upper filters, no IG_, just our HID minidriver.</item>
/// </list>
///
/// <para>Critical race fixes baked in:</para>
/// <list type="bullet">
/// <item><b>ControllerIndex registry write:</b> the driver reads this from
/// <c>Device Parameters\ControllerIndex</c> at startup to know which
/// per-instance shared section to attach to.</item>
/// <item><b>HID-child wait:</b> after driver install, we wait for the HID
/// interface to actually arrive before returning, otherwise sequential
/// controller creation races and PnP arrival order doesn't match creation
/// order.</item>
/// </list>
///
/// <para><b>ContainerID note:</b> root-enumerated virtual devices get the
/// <c>{00000000-0000-0000-FFFF-FFFFFFFFFFFF}</c> null-sentinel container
/// on Win11 26200. Attempts to set a real ContainerID from user mode
/// (CM API, registry writes to <c>Enum\&lt;instId&gt;\ContainerID</c>, INF
/// directives) are all refused or silently ignored by PnP. The documented
/// workaround (DeviceOverrides with Removable=1) also doesn't trigger
/// container generation for root devices without a location path on this
/// build. UMDF2 cannot act as a bus driver, so there's no user-mode path
/// to provide a real ContainerID. Consumers that dedupe on ContainerID
/// will treat our virtuals as individually-containerless devices.</para>
/// </summary>
internal static class DeviceNodeCreator
{
    // T34 — per-hwId DeviceOverrides write dedup. Same hwId = same override
    // path, so writing the same Removable=1 value more than once per process
    // is wasted work. ConcurrentBag-flavored dedup (HashSet behind a lock).
    private static readonly System.Collections.Generic.HashSet<string> s_deviceOverridesWritten =
        new(StringComparer.OrdinalIgnoreCase);
    private static readonly object s_deviceOverridesLock = new();
    /// <summary>HID class GUID — every virtual controller is created in this class.</summary>
    private static readonly Guid HIDClassGuid =
        new Guid("745a17a0-74d3-11d0-b6fe-00a0c90f57da");

    /// <summary>Result of creating a device node — instance ID + the in-process device-info handle.
    /// On success the caller can pass the instanceId to e.g. <c>WaitForHidChild</c> for downstream waits.
    /// On failure InstanceId is null.</summary>
    public readonly struct Result
    {
        public readonly bool Success;
        public readonly string? InstanceId;
        public Result(bool success, string? instanceId) { Success = success; InstanceId = instanceId; }
    }

    /// <summary>Create a virtual device node for the given profile and assign it the
    /// given controller index. After this returns successfully:
    /// <list type="bullet">
    /// <item>The device exists at <c>ROOT\&lt;enumerator&gt;\&lt;instId&gt;</c></item>
    /// <item>Its <c>Device Parameters\ControllerIndex</c> = <paramref name="controllerIndex"/></item>
    /// <item>The driver is installed and bound</item>
    /// <item>The HID child interface has arrived (so the next per-controller
    ///       setup can start without racing PnP)</item>
    /// </list>
    /// </summary>
    public static Result CreateDeviceNode(ControllerProfile profile, string infPath, int controllerIndex)
    {
        throw new NotSupportedException("Legacy root enumeration is not used by the supported Xbox profiles.");
    }

    // ── private P/Invokes ────────────────────────────────────────────────

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

    [DllImport("newdev.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool UpdateDriverForPlugAndPlayDevicesW(IntPtr hwndParent, string HardwareId,
        string FullInfPath, int InstallFlags, out bool RebootRequired);

    [DllImport("CfgMgr32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern uint CM_Locate_DevNodeW(out uint pdnDevInst, string pDeviceID, uint ulFlags);
}
