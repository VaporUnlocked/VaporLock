using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace VaporLockControllerHost;

internal static class ControllerWindowsPackages
{
    private static string WindowsInf => Path.Combine(Path.GetDirectoryName(Environment.SystemDirectory)!, "INF");
    private static string DriverStore => Path.Combine(Environment.SystemDirectory, "DriverStore", "FileRepository");
    private static readonly Regex OemName = new(@"\Aoem[0-9]+\.inf\z", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    internal static void RefuseForeignPackages(ControllerComponentRecord? owned)
    {
        var count = 0;
        foreach (var path in Directory.EnumerateFiles(WindowsInf, "oem*.inf", SearchOption.TopDirectoryOnly))
        {
            if (++count > 16384) throw new IOException("Windows driver inventory exceeds its supported bound.");
            using var file = OpenSystemInput(path, WindowsInf);
            if (file.Length > 2 * 1024 * 1024) continue;
            using var reader = new StreamReader(file, Encoding.UTF8, detectEncodingFromByteOrderMarks: true, leaveOpen: true);
            var text = reader.ReadToEnd();
            if (!text.Contains("HIDMaestro", StringComparison.OrdinalIgnoreCase) && !text.Contains("HMXInput.dll", StringComparison.OrdinalIgnoreCase)) continue;
            var package = owned?.Packages.SingleOrDefault(value => value.PublishedInf.Equals(Path.GetFileName(path), StringComparison.OrdinalIgnoreCase));
            if (package is null)
                throw new IOException("Existing HIDMaestro driver packages cannot be confirmed as VaporLock-managed. They were not adopted or removed.");
            Verify(package, owned!);
        }
    }

    internal static ControllerPackageEntry Import(string original, ControllerComponentRecord record)
    {
        if (original is not ("hidmaestro.inf" or "hidmaestro_xusb.inf")) throw new IOException("Unexpected controller package.");
        var inf = Path.Combine(record.PayloadDirectory, original);
        var destination = new StringBuilder(32768);
        // No overwrite, force removal, catalog-only import or source deletion.
        if (!SetupCopyOEMInfW(inf, null, 1, 8, destination, destination.Capacity, out _, IntPtr.Zero))
        {
            var error = Marshal.GetLastWin32Error();
            throw new Win32Exception(error, "Windows rejected the controller package. Existing packages, Code Integrity and signing policies were preserved.");
        }
        var published = Path.GetFileName(destination.ToString());
        if (!OemName.IsMatch(published)) throw new IOException("Windows did not return a canonical published controller INF name.");
        var stored = StoreInf(published);
        var directory = Path.GetDirectoryName(stored)!;
        var driver = original == "hidmaestro.inf" ? "HIDMaestro.dll" : "HMXInput.dll";
        var catalog = Path.ChangeExtension(original, ".cat");
        var result = new ControllerPackageEntry(original, published, directory,
            record.PayloadHashes[original], record.PayloadHashes[driver], record.PayloadHashes[catalog], true, false);
        Verify(result, record); return result;
    }

    internal static void Verify(ControllerPackageEntry package, ControllerComponentRecord record)
    {
        if (!OemName.IsMatch(package.PublishedInf) || package.PriorPresent || !package.ImportConfirmed
            || package.OriginalInf is not ("hidmaestro.inf" or "hidmaestro_xusb.inf"))
            throw new IOException("Controller package ownership is invalid.");
        var stored = StoreInf(package.PublishedInf);
        if (!Path.GetDirectoryName(stored)!.Equals(Path.GetFullPath(package.StoreDirectory), StringComparison.OrdinalIgnoreCase)
            || !Path.GetFileName(stored).Equals(package.OriginalInf, StringComparison.OrdinalIgnoreCase))
            throw new IOException("The published controller package no longer maps to its recorded store identity.");
        using var published = OpenSystemInput(Path.Combine(WindowsInf, package.PublishedInf), WindowsInf);
        using var inf = OpenSystemInput(stored, DriverStore);
        var driverName = package.OriginalInf == "hidmaestro.inf" ? "HIDMaestro.dll" : "HMXInput.dll";
        var driverPath = Path.Combine(package.StoreDirectory, driverName);
        var catalogPath = Path.Combine(package.StoreDirectory, Path.ChangeExtension(package.OriginalInf, ".cat"));
        using var driver = OpenSystemInput(driverPath, DriverStore);
        using var catalog = OpenSystemInput(catalogPath, DriverStore);
        if (ControllerStagedPayload.Digest(published) != package.InfSHA256 || ControllerStagedPayload.Digest(inf) != package.InfSHA256
            || ControllerStagedPayload.Digest(driver) != package.DriverSHA256 || ControllerStagedPayload.Digest(catalog) != package.CatalogSHA256
            || record.PayloadHashes[package.OriginalInf] != package.InfSHA256 || record.PayloadHashes[driverName] != package.DriverSHA256
            || record.PayloadHashes[Path.ChangeExtension(package.OriginalInf, ".cat")] != package.CatalogSHA256)
            throw new CryptographicException("The Windows controller package bytes differ from their protected provenance.");
        ControllerSignatureVerifier.VerifyPackage(catalogPath, driverPath, driver, inf, record.CertificateDer);
    }

    internal static void Remove(ControllerPackageEntry package, ControllerComponentRecord record)
    {
        if (!ControllerStateQueries.FilePresent(Path.Combine(WindowsInf, package.PublishedInf)))
        {
            // A previous positively owned removal may have completed before
            // its completion marker. Do not substitute a same-name package.
            if (!record.Events.Any(entry => entry.Kind == "remove-package" && entry.Subject == package.PublishedInf && entry.State == "intent"))
                throw new IOException("Controller package disappeared without an owned removal intent.");
            return;
        }
        Verify(package, record);
        // Flags0 deliberately preserves in-use packages; no SUOI_FORCEDELETE.
        if (!SetupUninstallOEMInfW(package.PublishedInf, 0, IntPtr.Zero))
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Controller package removal is incomplete or needs a restart. It was not forced.");
        if (ControllerStateQueries.FilePresent(Path.Combine(WindowsInf, package.PublishedInf)))
            throw new IOException("Windows still lists the controller package. Removal remains pending.");
    }

    private static string StoreInf(string published)
    {
        var buffer = new StringBuilder(32768);
        if (!SetupGetInfDriverStoreLocationW(Path.Combine(WindowsInf, published), IntPtr.Zero, null,
                buffer, buffer.Capacity, out _)) throw new Win32Exception(Marshal.GetLastWin32Error());
        var path = Path.GetFullPath(buffer.ToString());
        if (!path.StartsWith(DriverStore + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Windows returned a controller package outside DriverStore.");
        return path;
    }

    private static FileStream OpenSystemInput(string path, string root)
    {
        path = Path.GetFullPath(path); root = Path.GetFullPath(root);
        if (!path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Controller package input is outside its Windows store.");
        foreach (var parent in new FileInfo(path).Directory!.EnumerateAncestors())
        {
            if ((parent.Attributes & FileAttributes.ReparsePoint) != 0) throw new IOException("Linked Windows package directory rejected.");
            if (parent.FullName.Equals(root, StringComparison.OrdinalIgnoreCase)) break;
        }
        if ((File.GetAttributes(path) & (FileAttributes.ReparsePoint | FileAttributes.Directory)) != 0)
            throw new IOException("Linked Windows package member rejected.");
        return new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
    }

    private static IEnumerable<DirectoryInfo> EnumerateAncestors(this DirectoryInfo directory)
    { for (var current = directory; current is not null; current = current.Parent) yield return current; }

    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetupCopyOEMInfW(string source, string? media, uint mediaType, uint style,
        StringBuilder destination, int size, out uint required, IntPtr component);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetupGetInfDriverStoreLocationW(string filename, IntPtr platform, string? locale,
        StringBuilder destination, int size, out uint required);
    [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetupUninstallOEMInfW(string inf, uint flags, IntPtr reserved);
}
