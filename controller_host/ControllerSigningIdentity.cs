using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Security.Principal;

namespace VaporLockControllerHost;

// Only the component transaction may call Create after durably recording the
// random component/key identity. No PFX export, subject-name adoption or rotation.
internal sealed class ControllerSigningIdentity : IDisposable
{
    internal const string ProviderName = "Microsoft Software Key Storage Provider";
    internal const string CodeSigningOid = "1.3.6.1.5.5.7.3.3";
    private const string KeySecurity = "O:BAG:SYD:P(A;;GA;;;SY)(A;;GA;;;BA)";
    private const int OwnerAndDacl = 5;
    private readonly CngKey key;
    private readonly RSACng rsa = null!;
    internal X509Certificate2 Certificate { get; } = null!;
    internal string CertificateSHA256 => Convert.ToHexStringLower(SHA256.HashData(Certificate.RawData));
    internal string SpkiSHA256 => Convert.ToHexStringLower(SHA256.HashData(rsa.ExportSubjectPublicKeyInfo()));
    internal string KeyName => key.KeyName!;

    private ControllerSigningIdentity(CngKey ownedKey, X509Certificate2? certificate, Guid component)
    {
        key = ownedKey;
        try
        {
            VerifyKey(key);
            rsa = new RSACng(key);
            if (certificate is null)
            {
                var request = new CertificateRequest($"CN=VaporLock Controller {component:N}", rsa,
                    HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
                request.CertificateExtensions.Add(new X509BasicConstraintsExtension(false, false, 0, true));
                request.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.DigitalSignature, true));
                request.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension(
                    new OidCollection { new Oid(CodeSigningOid) }, true));
                request.CertificateExtensions.Add(new X509SubjectKeyIdentifierExtension(request.PublicKey, false));
                Certificate = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-5), DateTimeOffset.UtcNow.AddYears(3));
            }
            else Certificate = certificate.CopyWithPrivateKey(rsa);
            VerifyCertificate(Certificate, rsa);
        }
        catch { Certificate?.Dispose(); rsa?.Dispose(); throw; }
    }

    internal static ControllerSigningIdentity Create(Guid component, string recordedKeyName)
    {
        ValidateKeyName(component, recordedKeyName);
        var descriptor = new RawSecurityDescriptor(KeySecurity);
        var bytes = new byte[descriptor.BinaryLength];
        descriptor.GetBinaryForm(bytes, 0);
        var parameters = new CngKeyCreationParameters
        {
            Provider = new CngProvider(ProviderName),
            KeyCreationOptions = CngKeyCreationOptions.MachineKey,
            ExportPolicy = CngExportPolicies.None,
            KeyUsage = CngKeyUsages.Signing,
        };
        parameters.Parameters.Add(new CngProperty("Length", BitConverter.GetBytes(3072), CngPropertyOptions.None));
        // NCryptSetProperty flags for Security Descr are OWNER|DACL, NOT
        // NCRYPT_PERSIST_FLAG (which Microsoft forbids for this property).
        parameters.Parameters.Add(new CngProperty("Security Descr", bytes, (CngPropertyOptions)OwnerAndDacl));
        var owned = CngKey.Create(CngAlgorithm.Rsa, recordedKeyName, parameters);
        try { return new ControllerSigningIdentity(owned, null, component); }
        catch (Exception error)
        {
            // This is the exact handle returned by NEW key creation without
            // overwrite permission, not an adopted/reopened existing identity.
            try { owned.Delete(); }
            catch (Exception cleanup) { throw new AggregateException("New controller key creation failed and cleanup is incomplete.", error, cleanup); }
            finally { owned.Dispose(); }
            throw;
        }
    }

    internal static ControllerSigningIdentity Open(Guid component, string recordedKeyName,
        byte[] recordedDer, string expectedDerSHA256, string expectedSpkiSHA256)
    {
        ValidateKeyName(component, recordedKeyName);
        if (Convert.ToHexStringLower(SHA256.HashData(recordedDer)) != expectedDerSHA256)
            throw new CryptographicException("Controller signer provenance does not match its protected record.");
        using var publicCertificate = X509CertificateLoader.LoadCertificate(recordedDer);
        var owned = CngKey.Open(recordedKeyName, new CngProvider(ProviderName), CngKeyOpenOptions.MachineKey);
        ControllerSigningIdentity result;
        try { result = new ControllerSigningIdentity(owned, publicCertificate, component); }
        catch { owned.Dispose(); throw; } // NEVER delete an existing key on readback failure
        if (result.SpkiSHA256 != expectedSpkiSHA256)
        {
            result.Dispose();
            throw new CryptographicException("The recorded controller key was replaced.");
        }
        return result;
    }

    private static void ValidateKeyName(Guid component, string name)
    {
        if (component == Guid.Empty || name != "VaporLock.Controller." + component.ToString("N"))
            throw new CryptographicException("Invalid controller component signing identity.");
    }

    private static void VerifyKey(CngKey owned)
    {
        if (!owned.IsMachineKey || owned.IsEphemeral || owned.Provider.Provider != ProviderName
            || owned.Algorithm != CngAlgorithm.Rsa || owned.KeySize != 3072
            || owned.ExportPolicy != CngExportPolicies.None || owned.KeyUsage != CngKeyUsages.Signing)
            throw new CryptographicException("Controller signing key policy is unsupported or changed.");
        var size = 0;
        var status = NCryptGetProperty(owned.Handle, "Security Descr", null, 0, out size, OwnerAndDacl);
        if (status != 0 || size <= 0 || size > 65536) throw new CryptographicException(status);
        var bytes = new byte[size];
        status = NCryptGetProperty(owned.Handle, "Security Descr", bytes, bytes.Length, out size, OwnerAndDacl);
        if (status != 0) throw new CryptographicException(status);
        ValidateSecurityDescriptor(new RawSecurityDescriptor(bytes, 0));
    }

    internal static void ValidateSecurityDescriptor(RawSecurityDescriptor descriptor)
    {
        var admin = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
        var system = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
        var acl = descriptor.DiscretionaryAcl;
        if ((descriptor.Owner != admin && descriptor.Owner != system)
            || (descriptor.ControlFlags & ControlFlags.DiscretionaryAclPresent) == 0
            || (descriptor.ControlFlags & ControlFlags.DiscretionaryAclProtected) == 0
            || acl is null || acl.Count != 2)
            throw new CryptographicException("The controller signing key is not administrator protected.");
        var found = new HashSet<string>();
        foreach (GenericAce raw in acl)
        {
            if (raw is not CommonAce ace || ace.AceQualifier != AceQualifier.AccessAllowed
                || ace.AceFlags != AceFlags.None || ace.IsCallback
                // Microsoft Software KSP may return this exact expanded form
                // of the requested GA grant. Accept no subsets or extra bits.
                || (unchecked((uint)ace.AccessMask) != 0x10000000u
                    && unchecked((uint)ace.AccessMask) != 0xd01f01ffu)
                || (ace.SecurityIdentifier != admin && ace.SecurityIdentifier != system)
                || !found.Add(ace.SecurityIdentifier.Value))
                throw new CryptographicException("Unexpected controller signing-key access rule.");
        }
    }

    private static void VerifyCertificate(X509Certificate2 certificate, RSA privateKey)
    {
        using var publicKey = certificate.GetRSAPublicKey();
        if (publicKey is null || !publicKey.ExportSubjectPublicKeyInfo().AsSpan().SequenceEqual(privateKey.ExportSubjectPublicKeyInfo())
            || DateTime.UtcNow < certificate.NotBefore.ToUniversalTime() || DateTime.UtcNow >= certificate.NotAfter.ToUniversalTime()
            || certificate.SignatureAlgorithm.Value != "1.2.840.113549.1.1.11"
            || certificate.Extensions.OfType<X509BasicConstraintsExtension>().SingleOrDefault()?.CertificateAuthority != false
            || certificate.Extensions.OfType<X509KeyUsageExtension>().SingleOrDefault()?.KeyUsages != X509KeyUsageFlags.DigitalSignature)
            throw new CryptographicException("Controller signing certificate no longer matches its key or policy.");
        var eku = certificate.Extensions.OfType<X509EnhancedKeyUsageExtension>().SingleOrDefault();
        if (eku is null || eku.EnhancedKeyUsages.Count != 1 || eku.EnhancedKeyUsages[0].Value != CodeSigningOid)
            throw new CryptographicException("Unexpected controller signer purpose.");
    }

    public void Dispose() { Certificate.Dispose(); rsa.Dispose(); key.Dispose(); }

    [DllImport("ncrypt.dll", CharSet = CharSet.Unicode)]
    private static extern int NCryptGetProperty(Microsoft.Win32.SafeHandles.SafeNCryptKeyHandle handle,
        string property, byte[]? output, int outputSize, out int requiredSize, int flags);
}
