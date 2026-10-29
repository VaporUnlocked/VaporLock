using System;
namespace HIDMaestro.Internal;
// VaporLock integration: the SDK has no installer or extraction authority.
public static class DriverBuilder
{
    public static string? StagingDir => VaporLockIntegration.PreparedDirectory;
    public static string BuildDir => EnsureExtracted();
    public static string EnsureExtracted() => VaporLockIntegration.RequirePrepared();
    public static bool IsDriverInstalled() => VaporLockIntegration.IsReady();
    internal static bool WillTakeFastPath() => IsDriverInstalled();
    private static Exception Disabled() => new NotSupportedException("Use VaporLock's explicit component transaction.");
    public static void EnsureTestCertificate() => throw Disabled();
    public static bool SignDrivers() => throw Disabled();
    public static bool GenerateCatalogs() => throw Disabled();
    public static bool InstallDrivers() => throw Disabled();
    public static void RemoveOldDriverPackages() => throw Disabled();
    public static bool FullDeploy(bool rebuild = true) => throw Disabled();
}
