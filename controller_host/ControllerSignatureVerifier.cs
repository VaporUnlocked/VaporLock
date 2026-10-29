using System.ComponentModel;
using System.Formats.Asn1;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Microsoft.Win32.SafeHandles;

namespace VaporLockControllerHost;

// Signature mathematics and exact catalog membership are checked BEFORE adding
// machine trust. The known signer comes only from our protected install record,
// never from a failed/untrusted catalog's self-asserted certificate.
internal static class ControllerSignatureVerifier
{
    private const string Sha256Oid = "2.16.840.1.101.3.4.2.1";
    private const string Sha1Oid = "1.3.14.3.2.26";
    internal static readonly Guid PeSubject = new("c689aab8-8e78-11d0-8c47-00c04fc295ee");
    internal static readonly Guid InfSubject = new("de351a42-8e59-11d0-8c47-00c04fc295ee");
    internal sealed record CatalogMemberDigest(string Algorithm, byte[] Digest, string Tag, Guid Subject);

    internal static void VerifyPackage(string catalog, string driver, FileStream driverHandle,
        FileStream infHandle, byte[] recordedCertificateDer)
    {
        VerifyPe(driver, driverHandle, recordedCertificateDer);
        VerifyMessage(catalog, embedded: false, recordedCertificateDer, content: false);
        var expected = new[]
        {
            new CatalogMemberDigest(Sha256Oid, Hash(driverHandle), "", PeSubject),
            new CatalogMemberDigest(Sha256Oid, Hash(infHandle), "", InfSubject),
            new CatalogMemberDigest(Sha1Oid, Hash(driverHandle, "SHA1"), "", PeSubject),
            new CatalogMemberDigest(Sha1Oid, Hash(infHandle, "SHA1"), "", InfSubject),
        };
        var handle = CryptCATOpen(catalog, 0, IntPtr.Zero, 0, 0);
        if (handle == new IntPtr(-1) || handle == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            var found = new List<CatalogMemberDigest>();
            var pointer = IntPtr.Zero;
            while ((pointer = CryptCATEnumerateMember(handle, pointer)) != IntPtr.Zero)
            {
                if (found.Count >= 4) throw new CryptographicException("Unexpected controller catalog size.");
                var member = Marshal.PtrToStructure<CatMember>(pointer);
                if (member.Size != Marshal.SizeOf<CatMember>() || member.IndirectData == IntPtr.Zero)
                    throw new CryptographicException("Catalog member has invalid structure or no digest.");
                var indirect = Marshal.PtrToStructure<IndirectData>(member.IndirectData);
                var oid = ReadText(indirect.Algorithm.ObjectId, 96, wide: false);
                found.Add(new(oid, ReadDigest(oid, indirect.Digest), ReadText(member.ReferenceTag, 64, wide: true), member.Subject));
            }
            if (Marshal.GetLastWin32Error() != 0) throw new Win32Exception(Marshal.GetLastWin32Error());
            ValidateCatalogMembers(expected, found);
        }
        finally { CryptCATClose(handle); }
    }

    private static void VerifyPe(string path, FileStream held, byte[] certificate)
    {
        var content = VerifyMessage(path, embedded: true, certificate, content: true);
        var reader = new AsnReader(content, AsnEncodingRules.DER);
        var indirect = reader.ReadSequence();
        indirect.ReadEncodedValue(); // SpcAttributeTypeAndOptionalValue
        var digestInfo = indirect.ReadSequence();
        var algorithm = digestInfo.ReadSequence();
        if (algorithm.ReadObjectIdentifier() != Sha256Oid) throw new CryptographicException("Controller PE digest is not SHA256.");
        if (algorithm.HasData) algorithm.ReadNull();
        algorithm.ThrowIfNotEmpty();
        var digest = digestInfo.ReadOctetString();
        digestInfo.ThrowIfNotEmpty(); indirect.ThrowIfNotEmpty(); reader.ThrowIfNotEmpty();
        if (!CryptographicOperations.FixedTimeEquals(digest, Hash(held)))
            throw new CryptographicException("Controller PE contents differ from their signed indirect digest.");
    }

    private static byte[] VerifyMessage(string path, bool embedded, byte[] certificate, bool content)
    {
        if (!CryptQueryObject(1, path, embedded ? 0x400u : 0x100u, 2, 0,
                out _, out _, out _, out var store, out var message, IntPtr.Zero))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        var context = IntPtr.Zero;
        try
        {
            var signers = MessageParameter(message, 5, 16);
            if (signers.Length != 4 || BitConverter.ToInt32(signers) != 1)
                throw new CryptographicException("Controller payload must have one explicit signer.");
            // Parse DER rather than returning pointer-bearing native structures
            // in movable managed arrays (CMSG_SIGNER_HASH_ALGORITHM_PARAM).
            var signerReader = new AsnReader(MessageParameter(message, 28, 128 * 1024), AsnEncodingRules.DER);
            var signer = signerReader.ReadSequence();
            signer.ReadInteger(); signer.ReadEncodedValue(); // version, signer identifier
            var signerAlgorithm = signer.ReadSequence();
            if (signerAlgorithm.ReadObjectIdentifier() != Sha256Oid)
                throw new CryptographicException("Controller signer digest must be SHA256.");
            if (signerAlgorithm.HasData) signerAlgorithm.ReadNull();
            signerAlgorithm.ThrowIfNotEmpty();
            context = CertCreateCertificateContext(1, certificate, certificate.Length);
            if (context == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
            var verification = new VerifySignature
            {
                Size = Marshal.SizeOf<VerifySignature>(), SignerType = 2, Signer = context,
            };
            if (!CryptMsgControl(message, 0, 19, ref verification))
                throw new CryptographicException("Controller signature does not verify with the recorded signing certificate.");
            return content ? MessageParameter(message, 2, 2 * 1024 * 1024) : Array.Empty<byte>();
        }
        finally
        {
            if (context != IntPtr.Zero) CertFreeCertificateContext(context);
            if (message != IntPtr.Zero) CryptMsgClose(message);
            if (store != IntPtr.Zero) CertCloseStore(store, 0);
        }
    }

    private static byte[] MessageParameter(IntPtr message, uint parameter, int maximum)
    {
        uint size = 0;
        if (!CryptMsgGetParam(message, parameter, 0, null, ref size) || size == 0 || size > maximum)
            throw new CryptographicException("Controller signature metadata is unsupported or oversized.");
        var bytes = new byte[size];
        if (!CryptMsgGetParam(message, parameter, 0, bytes, ref size) || size != bytes.Length)
            throw new CryptographicException("Controller signature metadata changed.");
        return bytes;
    }

    // Compatibility SHA1 members are checked only as redundant metadata. Both
    // SHA256 subjects remain mandatory; CMS and PE signatures stay SHA256.
    internal static void ValidateCatalogMembers(IReadOnlyList<CatalogMemberDigest> expected, IReadOnlyList<CatalogMemberDigest> actual)
    {
        if (expected.Count != 4 || actual.Count is < 2 or > 4)
            throw new CryptographicException("Unexpected controller catalog member count.");
        var expectedMap = new Dictionary<(string Algorithm, string Digest), Guid>();
        foreach (var item in expected)
        {
            var size = item.Algorithm == Sha256Oid ? 32 : item.Algorithm == Sha1Oid ? 20 : 0;
            if (size == 0 || item.Digest is null || item.Digest.Length != size
                || (item.Subject != PeSubject && item.Subject != InfSubject)
                || !expectedMap.TryAdd((item.Algorithm, Convert.ToHexString(item.Digest)), item.Subject))
                throw new CryptographicException("Ambiguous expected controller subject digest.");
        }
        foreach (var algorithm in new[] { Sha256Oid, Sha1Oid })
            if (!expected.Where(item => item.Algorithm == algorithm).Select(item => item.Subject).ToHashSet().SetEquals(new[] { PeSubject, InfSubject }))
                throw new CryptographicException("Expected controller subjects are incomplete.");
        var seen = new HashSet<(string Algorithm, string Digest)>();
        foreach (var item in actual)
        {
            var size = item.Algorithm == Sha256Oid ? 32 : item.Algorithm == Sha1Oid ? 20 : 0;
            if (size == 0 || item.Digest is null || item.Digest.Length != size)
                throw new CryptographicException("Unsupported controller member digest.");
            var hex = Convert.ToHexString(item.Digest);
            var key = (item.Algorithm, hex);
            if (!hex.Equals(item.Tag, StringComparison.OrdinalIgnoreCase)
                || !expectedMap.TryGetValue(key, out var subject) || subject != item.Subject || !seen.Add(key))
                throw new CryptographicException("Unapproved, mismatched or duplicate controller catalog member.");
        }
        if (expectedMap.Keys.Where(key => key.Algorithm == Sha256Oid).Any(key => !seen.Contains(key)))
            throw new CryptographicException("Controller catalog lacks a mandatory SHA256 payload member.");
    }

    private static byte[] Hash(FileStream file, string algorithm = "SHA256")
    {
        var expectedSize = algorithm == "SHA256" ? 32 : algorithm == "SHA1" ? 20
            : throw new CryptographicException("Unsupported controller subject hash context.");
        var subsystem = new Guid("F750E6C3-38EE-11D1-85E5-00C04FC295EE");
        if (!CryptCATAdminAcquireContext2(out var admin, ref subsystem, algorithm, IntPtr.Zero, 0))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            file.Position = 0;
            uint size = 0;
            if (!CryptCATAdminCalcHashFromFileHandle2(admin, file.SafeFileHandle, ref size, null, 0) || size != expectedSize)
                throw new CryptographicException("Windows could not calculate the explicit controller subject digest.");
            var digest = new byte[size];
            file.Position = 0;
            if (!CryptCATAdminCalcHashFromFileHandle2(admin, file.SafeFileHandle, ref size, digest, 0) || size != expectedSize)
                throw new Win32Exception(Marshal.GetLastWin32Error());
            return digest;
        }
        finally { CryptCATAdminReleaseContext(admin, 0); }
    }

    private static string ReadText(IntPtr pointer, int maximum, bool wide)
    {
        if (pointer == IntPtr.Zero) throw new CryptographicException("Missing controller catalog text.");
        var text = new System.Text.StringBuilder();
        for (var index = 0; index <= maximum; index++)
        {
            var value = wide ? unchecked((ushort)Marshal.ReadInt16(pointer, index * 2)) : Marshal.ReadByte(pointer, index);
            if (value == 0) return text.ToString();
            if (index == maximum) break;
            text.Append((char)value);
        }
        throw new CryptographicException("Controller catalog text exceeds its bound.");
    }

    private static byte[] ReadDigest(string algorithm, Blob digest)
    {
        var size = algorithm == Sha256Oid ? 32 : algorithm == Sha1Oid ? 20 : 0;
        if (size == 0 || digest.Size != size || digest.Data == IntPtr.Zero)
            throw new CryptographicException("Unexpected controller catalog digest algorithm.");
        var bytes = new byte[size]; Marshal.Copy(digest.Data, bytes, 0, size); return bytes;
    }

    [StructLayout(LayoutKind.Sequential)] private struct Blob { public uint Size; public IntPtr Data; }
    [StructLayout(LayoutKind.Sequential)] private struct Algorithm { public IntPtr ObjectId; public Blob Parameters; }
    [StructLayout(LayoutKind.Sequential)] private struct Attribute { public IntPtr ObjectId; public Blob Value; }
    [StructLayout(LayoutKind.Sequential)] private struct IndirectData { public Attribute Data; public Algorithm Algorithm; public Blob Digest; }
    [StructLayout(LayoutKind.Sequential)] private struct VerifySignature { public int Size; public IntPtr Provider; public uint SignerIndex, SignerType; public IntPtr Signer; }
    [StructLayout(LayoutKind.Sequential)] private struct CatMember
    {
        public uint Size; public IntPtr ReferenceTag, FileName; public Guid Subject;
        public uint Flags; public IntPtr IndirectData; public uint CertVersion, Reserved;
        public IntPtr ReservedPointer; public Blob EncodedIndirectData, EncodedMember;
    }
    [DllImport("crypt32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool CryptQueryObject(uint type, string value, uint contentFlags, uint formatFlags, uint flags, out uint encoding, out uint content, out uint format, out IntPtr store, out IntPtr message, IntPtr context);
    [DllImport("crypt32.dll", SetLastError = true)] private static extern bool CryptMsgGetParam(IntPtr message, uint parameter, uint index, byte[]? data, ref uint size);
    [DllImport("crypt32.dll", SetLastError = true)] private static extern bool CryptMsgControl(IntPtr message, uint flags, uint control, ref VerifySignature value);
    [DllImport("crypt32.dll", SetLastError = true)] private static extern IntPtr CertCreateCertificateContext(uint encoding, byte[] bytes, int length);
    [DllImport("crypt32.dll")] private static extern bool CertFreeCertificateContext(IntPtr context);
    [DllImport("crypt32.dll")] private static extern bool CertCloseStore(IntPtr store, uint flags);
    [DllImport("crypt32.dll")] private static extern bool CryptMsgClose(IntPtr message);
    [DllImport("wintrust.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CryptCATOpen(string path, uint flags, IntPtr provider, uint version, uint encoding);
    [DllImport("wintrust.dll", SetLastError = true)] private static extern IntPtr CryptCATEnumerateMember(IntPtr catalog, IntPtr previous);
    [DllImport("wintrust.dll")] private static extern bool CryptCATClose(IntPtr catalog);
    [DllImport("wintrust.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern bool CryptCATAdminAcquireContext2(out IntPtr admin, ref Guid subsystem, string hashAlgorithm, IntPtr policy, uint flags);
    [DllImport("wintrust.dll", SetLastError = true)] private static extern bool CryptCATAdminCalcHashFromFileHandle2(IntPtr admin, SafeFileHandle file, ref uint size, byte[]? hash, uint flags);
    [DllImport("wintrust.dll")] private static extern bool CryptCATAdminReleaseContext(IntPtr admin, uint flags);
}
