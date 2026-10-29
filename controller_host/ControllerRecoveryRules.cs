namespace VaporLockControllerHost;

internal static class ControllerRecoveryRules
{
    internal static void ValidateBinding(ControllerComponentRecord owned, ControllerCreationEntry creation,
        ControllerDeviceEntry? recorded, string currentInf, string currentService)
    {
        var package = owned.Packages.SingleOrDefault(item => item.PublishedInf.Equals(currentInf, StringComparison.OrdinalIgnoreCase));
        if (recorded is not null)
        {
            if (package is null || !recorded.InstanceId.Equals(creation.InstanceId, StringComparison.OrdinalIgnoreCase)
                || !recorded.InfName.Equals(currentInf, StringComparison.OrdinalIgnoreCase)
                || !recorded.Service.Equals(currentService, StringComparison.OrdinalIgnoreCase))
                throw new IOException("The recorded controller's driver binding changed; it was preserved.");
            return;
        }
        if (!owned.Events.Any(item => item.Kind == "create-device" && item.State == "intent" && item.Subject == creation.Suffix
            && !owned.Events.Any(done => done.Operation == item.Operation && done.State == "complete")))
            throw new IOException("An unrecorded controller has no pending creation authority.");
        if (currentInf.Length == 0 && currentService.Length == 0) return; // proven pending, still unbound
        if (package is null || !(package.OriginalInf == "hidmaestro.inf" && currentService.Equals("mshidumdf", StringComparison.OrdinalIgnoreCase)
            || package.OriginalInf == "hidmaestro_xusb.inf" && currentService.Equals("WUDFRd", StringComparison.OrdinalIgnoreCase)))
            throw new IOException("The pending controller has an ambiguous or foreign driver binding; it was preserved.");
    }
    internal static void ValidateRetirement(ControllerComponentRecord owned, string? marker, string[] rootValues,
        string[] rootChildren, string? slotSession, string[] slotValues, string[] slotChildren)
    {
        var allowed = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "VaporLockSession", "DeviceInstanceId", "FunctionMode",
            "ReportDescriptor", "VendorId", "ProductId", "VersionNumber", "ProductString", "InputReportByteLength",
            "DeviceDescription", "LastSwdGamepadHResult" };
        if (marker != owned.Component.ToString("N") || rootValues.Any(name => name != "VaporLockComponentId")
            || rootChildren.Any(name => name != "Controller0") || slotChildren.Length != 0 || slotValues.Any(name => !allowed.Contains(name))
            || rootChildren.Contains("Controller0") && (slotSession is null || !Guid.TryParseExact(slotSession, "N", out _)
                || !owned.Events.Any(item => item.Kind == "reserve-slot" && item.Subject == "Controller0:" + slotSession))
            || owned.Creations.Any(item => !item.RemovalConfirmed) || owned.Devices.Any(item => !item.RemovalConfirmed)
            || owned.Events.Any(item => item.Kind == "create-device" && item.State == "intent"
                && !owned.Events.Any(done => done.Operation == item.Operation && done.State == "complete")))
            throw new IOException("Owned controller configuration recovery is incomplete or ambiguous; shared state was preserved.");
    }
}
