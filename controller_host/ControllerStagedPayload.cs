using System.ComponentModel;
using System.Diagnostics;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32.SafeHandles;

namespace VaporLockControllerHost;

// Only fixed embedded tools/resources can enter this component-owned stage.
// No SDK extraction, user-supplied signing path, PFX or automatically selected CN.
internal sealed class ControllerStagedPayload : IDisposable
{
    private readonly ControllerProtectedStorage storage;
    private readonly List<FileStream> held = new();
    private readonly Dictionary<string, FileStream> resources = new(StringComparer.OrdinalIgnoreCase);
    private readonly string tools;
    internal ControllerStagedPayload(ControllerProtectedStorage owner)
    {
        storage = owner;
        tools = storage.CreateChild("tools-95c6d3ec0d41cd7b");
        foreach (var resource in ControllerResources.Files)
        {
            var target = Path.Combine(tools, resource.FileName);
            if (!File.Exists(target))
            {
                using var source = Assembly.GetExecutingAssembly().GetManifestResourceStream(resource.Name)
                    ?? throw new IOException("A compiled controller resource is missing.");
                if (source.Length != resource.Bytes) throw new CryptographicException("A compiled controller resource changed size.");
                using var output = storage.CreateOutput(target); source.CopyTo(output); output.Flush(true);
            }
            var lease = storage.HoldInput(target);
            try
            {
                if (lease.Length != resource.Bytes || Digest(lease) != resource.SHA256)
                    throw new CryptographicException("An existing protected controller resource differs from its compiled identity.");
                held.Add(lease);
                resources.Add(resource.FileName, lease);
            }
            catch { lease.Dispose(); throw; }
        }
        if (!new HashSet<string>(Directory.EnumerateFiles(tools).Select(Path.GetFileName)!, StringComparer.OrdinalIgnoreCase)
                .SetEquals(ControllerResources.Files.Select(value => value.FileName)) || Directory.EnumerateDirectories(tools).Any())
            throw new IOException("Unlisted controller signing-tool content was found.");
    }

    internal ControllerPayloadProof Build(ControllerComponentRecord record, X509Certificate2 signer,
        Action<ControllerComponentRecord> save)
    {
        var generation = "payload-" + Guid.NewGuid().ToString("N");
        var directory = Path.Combine(storage.Root, generation);
        // Persist the intended new generation before creating or signing files.
        record.PayloadDirectory = directory; save(record);
        storage.CreateChild(generation);
        foreach (var name in new[] { "hidmaestro.inf", "hidmaestro_xusb.inf", "HIDMaestro.dll", "HMXInput.dll", "hmswd.exe" })
        {
            var input = resources[name];
            using var output = storage.CreateOutput(Path.Combine(directory, name));
            input.Position = 0;
            if (name.EndsWith(".inf", StringComparison.OrdinalIgnoreCase))
            {
                var original = Encoding.UTF8.GetString(ControllerProtectedStorage.ReadBytes(input, 16 * 1024));
                var pattern = new Regex(@"(?m)^ProviderName\s*=\s*""[^""\r\n]*""[^\S\r\n]*\r?$", RegexOptions.CultureInvariant);
                if (pattern.Matches(original).Count != 1) throw new IOException("Unexpected fixed controller INF provider field.");
                var amended = pattern.Replace(original, "ProviderName = \"VaporLock Controller " + record.Component.ToString("N") + "\"");
                output.Write(Encoding.UTF8.GetBytes(amended));
            }
            else input.CopyTo(output);
            output.Flush(true);
        }
        // Signtool uses exactly our My-store identity. My association/DER/key
        // policy is verified by the platform before this method is called.
        foreach (var name in new[] { "HIDMaestro.dll", "HMXInput.dll" })
            Run("signtool.exe", directory, "sign", "/fd", "SHA256", "/sm", "/s", "My", "/sha1", signer.Thumbprint, Path.Combine(directory, name));
        Run("Inf2Cat.exe", directory, "/driver:" + directory, "/os:10_X64");
        foreach (var name in new[] { "hidmaestro.cat", "hidmaestro_xusb.cat" })
            Run("signtool.exe", directory, "sign", "/fd", "SHA256", "/sm", "/s", "My", "/sha1", signer.Thumbprint, Path.Combine(directory, name));
        var payload = new Dictionary<string, FileStream>(StringComparer.OrdinalIgnoreCase);
        try
        {
            foreach (var name in new[] { "hidmaestro.inf", "hidmaestro_xusb.inf", "HIDMaestro.dll", "HMXInput.dll", "hidmaestro.cat", "hidmaestro_xusb.cat", "hmswd.exe" })
            {
                var path = Path.Combine(directory, name);
                // Trusted tools can create a new catalog with a token-default
                // owner. Atomically hold that generated file, then apply the
                // component descriptor; never do this to arbitrary input paths.
                ControllerGeneratedFile.Seal(path, directory);
                payload.Add(name, storage.HoldInput(path));
            }
            ControllerSignatureVerifier.VerifyPackage(Path.Combine(directory, "hidmaestro.cat"),
                Path.Combine(directory, "HIDMaestro.dll"), payload["HIDMaestro.dll"], payload["hidmaestro.inf"], record.CertificateDer);
            ControllerSignatureVerifier.VerifyPackage(Path.Combine(directory, "hidmaestro_xusb.cat"),
                Path.Combine(directory, "HMXInput.dll"), payload["HMXInput.dll"], payload["hidmaestro_xusb.inf"], record.CertificateDer);
            var hashes = payload.Where(pair => pair.Key != "hmswd.exe").ToDictionary(pair => pair.Key, pair => Digest(pair.Value), StringComparer.OrdinalIgnoreCase);
            held.AddRange(payload.Values); payload.Clear();
            return new(directory, hashes);
        }
        finally { foreach (var input in payload.Values) input.Dispose(); }
    }

    private void Run(string tool, string working, params string[] arguments)
    {
        var source = resources[tool];
        var identity = ControllerResources.Files.Single(value => value.FileName.Equals(tool, StringComparison.OrdinalIgnoreCase));
        if (Digest(source) != identity.SHA256) throw new CryptographicException("Controller tool identity changed.");
        var start = new ProcessStartInfo(Path.Combine(tools, tool)) { WorkingDirectory = working, UseShellExecute = false,
            CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true };
        foreach (var argument in arguments) start.ArgumentList.Add(argument);
        start.Environment.Clear();
        start.Environment["SystemRoot"] = Path.GetDirectoryName(Environment.SystemDirectory)!;
        start.Environment["PATH"] = Environment.SystemDirectory;
        start.Environment["TEMP"] = working; start.Environment["TMP"] = working;
        using var process = Process.Start(start) ?? throw new IOException("Windows did not start the fixed controller tool.");
        using var job = new ControllerChildJob(process);
        var log = new StringBuilder();
        var overflow = false;
        void Capture(object _, DataReceivedEventArgs args)
        {
            if (args.Data is null) return;
            lock (log)
            {
                if (log.Length + args.Data.Length > 128 * 1024) { overflow = true; return; }
                log.AppendLine(args.Data);
            }
        }
        process.OutputDataReceived += Capture; process.ErrorDataReceived += Capture;
        process.BeginOutputReadLine(); process.BeginErrorReadLine();
        if (!process.WaitForExit(60000))
        {
            process.Kill(entireProcessTree: true); process.WaitForExit(10000);
            throw new IOException("The fixed controller tool timed out: " + tool);
        }
        process.WaitForExit();
        if (overflow || process.ExitCode != 0)
            throw new IOException("Controller tool failed: " + tool + " (exit " + process.ExitCode + "). " + log.ToString()[..Math.Min(log.Length, 2048)]);
    }

    internal static string Digest(FileStream input)
    { input.Position = 0; return Convert.ToHexStringLower(SHA256.HashData(input)); }
    public void Dispose() { foreach (var input in held) input.Dispose(); held.Clear(); }
}

internal static class ControllerGeneratedFile
{
    internal static void Seal(string path, string generation)
    {
        path = Path.GetFullPath(path); generation = Path.GetFullPath(generation);
        if (Path.GetDirectoryName(path) != generation || !Path.GetFileName(generation).StartsWith("payload-", StringComparison.Ordinal)
            || !new[] { "hidmaestro.inf", "hidmaestro_xusb.inf", "HIDMaestro.dll", "HMXInput.dll", "hidmaestro.cat", "hidmaestro_xusb.cat", "hmswd.exe" }.Contains(Path.GetFileName(path)))
            throw new IOException("Only exact generated controller outputs can be sealed.");
        using var file = CreateFileW(path, 0x800e0000, 1, IntPtr.Zero, 3, 0x00200000, IntPtr.Zero);
        if (file.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
        if (!GetFileInformationByHandle(file, out var info) || (info.Attributes & 0x410) != 0 || info.Links != 1)
            throw new IOException("Linked generated controller output rejected.");
        var final = new StringBuilder(32768);
        var count = GetFinalPathNameByHandleW(file, final, final.Capacity, 0);
        if (count == 0 || count >= final.Capacity || !final.ToString().Equals("\\\\?\\" + path, StringComparison.OrdinalIgnoreCase))
            throw new IOException("Generated controller path identity changed.");
        if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(ControllerProtectedStorage.Security, 1, out var descriptor, out _))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            if (!GetSecurityDescriptorOwner(descriptor, out var owner, out _) || !GetSecurityDescriptorDacl(descriptor, out var present, out var dacl, out _) || !present)
                throw new Win32Exception(Marshal.GetLastWin32Error());
            var status = SetSecurityInfo(file, 1, 0x80000005, owner, IntPtr.Zero, dacl, IntPtr.Zero);
            if (status != 0) throw new Win32Exception((int)status);
        }
        finally { LocalFree(descriptor); }
    }
    [StructLayout(LayoutKind.Sequential)] private struct Info
    { public uint Attributes; public System.Runtime.InteropServices.ComTypes.FILETIME Created, Accessed, Written; public uint Volume, High, Low, Links, IndexHigh, IndexLow; }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetFileInformationByHandle(SafeFileHandle file, out Info info);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern uint GetFinalPathNameByHandleW(SafeFileHandle file, StringBuilder path, int size, uint flags);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool ConvertStringSecurityDescriptorToSecurityDescriptorW(string sddl, uint revision, out IntPtr descriptor, out uint size);
    [DllImport("advapi32.dll", SetLastError = true)] private static extern bool GetSecurityDescriptorOwner(IntPtr descriptor, out IntPtr owner, out bool defaulted);
    [DllImport("advapi32.dll", SetLastError = true)] private static extern bool GetSecurityDescriptorDacl(IntPtr descriptor, out bool present, out IntPtr dacl, out bool defaulted);
    [DllImport("advapi32.dll")] private static extern uint SetSecurityInfo(SafeFileHandle handle, int type, uint parts, IntPtr owner, IntPtr group, IntPtr dacl, IntPtr sacl);
    [DllImport("kernel32.dll")] private static extern IntPtr LocalFree(IntPtr pointer);
}
