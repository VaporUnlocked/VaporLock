using System.Runtime.InteropServices;
using System.Security.Cryptography.X509Certificates;

namespace VaporLockControllerHost;

internal sealed record ControllerSigningTools(string SignTool, string Inf2Cat)
{
    internal static ControllerSigningTools Find()
    {
        // Setup is elevated. Search only the standard machine-wide kit location,
        // never PATH, the current directory or a user-controlled kit location.
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        if (Path.IsPathFullyQualified(programFiles))
        {
            var tools = FindInstalledPair(Path.Combine(programFiles, "Windows Kits", "10", "bin"));
            if (tools is not null) return tools;
        }
        throw new IOException("Install a matching Microsoft Windows SDK and Windows Driver Kit (WDK) in the default location, then retry controller setup. Both kits are required to sign the local controller driver.");
    }

    private static ControllerSigningTools? FindInstalledPair(string bin)
    {
        if (Directory.Exists(bin))
        {
            var versions = Directory.EnumerateDirectories(bin)
                .Select(path => (Path: path, Version: Version.TryParse(Path.GetFileName(path), out var parsed) ? parsed : null))
                .Where(kit => kit.Version is not null)
                .OrderByDescending(kit => kit.Version);
            foreach (var kit in versions)
            {
                var signTool = FindTool(kit.Path, "signtool.exe");
                var inf2Cat = FindTool(kit.Path, "Inf2Cat.exe");
                if (signTool is not null && inf2Cat is not null)
                    return new(signTool, inf2Cat);
            }
        }
        return null;
    }

    private static string? FindTool(string kit, string name)
    {
        foreach (var architecture in new[] { "x64", "x86" })
        {
            var tool = Path.Combine(kit, architecture, name);
            if (File.Exists(tool) && IsTrustedMicrosoftBinary(tool))
                return tool;
        }
        return null;
    }

    private static bool IsTrustedMicrosoftBinary(string path)
    {
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        for (var location = path; !location.Equals(programFiles, StringComparison.OrdinalIgnoreCase);
             location = Path.GetDirectoryName(location)!)
        {
            if ((File.GetAttributes(location) & FileAttributes.ReparsePoint) != 0)
                return false;
        }

        var file = new WinTrustFileInfo { Size = (uint)Marshal.SizeOf<WinTrustFileInfo>(), FilePath = path };
        var filePointer = Marshal.AllocHGlobal(Marshal.SizeOf<WinTrustFileInfo>());
        Marshal.StructureToPtr(file, filePointer, false);
        try
        {
            var trust = new WinTrustData
            {
                Size = (uint)Marshal.SizeOf<WinTrustData>(),
                UIChoice = 2, // WTD_UI_NONE
                UnionChoice = 1, // WTD_CHOICE_FILE
                File = filePointer,
                ProviderFlags = 0x1000, // WTD_CACHE_ONLY_URL_RETRIEVAL
            };
            var verifyAuthenticode = new Guid("00AAC56B-CD44-11D0-8CC2-00C04FC295EE");
            if (WinVerifyTrust(new IntPtr(-1), in verifyAuthenticode, ref trust) != 0)
                return false;

            // CreateFromSignedFile extracts the Authenticode signer, whereas
            // X509CertificateLoader only loads certificate files or raw data.
#pragma warning disable SYSLIB0057
            using var source = X509Certificate.CreateFromSignedFile(path);
#pragma warning restore SYSLIB0057
            using var signer = X509CertificateLoader.LoadCertificate(source.GetRawCertData());
            return signer.GetNameInfo(X509NameType.SimpleName, false) == "Microsoft Corporation";
        }
        finally
        {
            Marshal.DestroyStructure<WinTrustFileInfo>(filePointer);
            Marshal.FreeHGlobal(filePointer);
        }
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WinTrustFileInfo
    {
        public uint Size;
        public string FilePath;
        public IntPtr FileHandle, KnownSubject;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct WinTrustData
    {
        public uint Size;
        public IntPtr PolicyCallbackData, SipClientData;
        public uint UIChoice, RevocationChecks, UnionChoice;
        public IntPtr File;
        public uint StateAction;
        public IntPtr StateData, UrlReference;
        public uint ProviderFlags, UIContext;
        public IntPtr SignatureSettings;
    }

    [DllImport("wintrust.dll", ExactSpelling = true)]
    private static extern int WinVerifyTrust(IntPtr window, in Guid action, ref WinTrustData data);
}
