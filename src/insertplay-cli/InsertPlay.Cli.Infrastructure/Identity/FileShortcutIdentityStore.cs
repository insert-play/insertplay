using System.Text.Json;
using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Infrastructure.Identity;

public sealed class FileShortcutIdentityStore(IFileSystem fileSystem) : IShortcutIdentityStore
{
    public bool TryGetOrCreate(
        string sdRoot,
        string appName,
        string? manifestGuid,
        out string shortcutGuid,
        out string error)
    {
        shortcutGuid = string.Empty;
        error = string.Empty;

        var manifestGuidNormalized = string.Empty;
        if (!string.IsNullOrWhiteSpace(manifestGuid))
        {
            if (!Guid.TryParse(manifestGuid, out var parsedManifestGuid))
            {
                error = "Manifest field 'shortcut.guid' is invalid. Use a valid UUID/GUID.";
                return false;
            }

            manifestGuidNormalized = parsedManifestGuid.ToString("D");
        }

        var identityPath = Path.Combine(sdRoot, "shortcut-identity.json");
        var legacyIdentityPath = Path.Combine(sdRoot, "insertplay", "shortcut-identity.json");

        ShortcutIdentity? existingIdentity = null;
        var existingIdentityPath = string.Empty;
        if (fileSystem.FileExists(identityPath))
            existingIdentityPath = identityPath;
        else if (fileSystem.FileExists(legacyIdentityPath))
            existingIdentityPath = legacyIdentityPath;

        if (!string.IsNullOrWhiteSpace(existingIdentityPath))
        {
            try
            {
                var existingJson = fileSystem.ReadAllText(existingIdentityPath);
                existingIdentity = JsonSerializer.Deserialize<ShortcutIdentity>(existingJson, new JsonSerializerOptions
                {
                    PropertyNameCaseInsensitive = true
                });
            }
            catch (Exception ex)
            {
                error = $"Failed to parse shortcut identity file: {ex.Message}";
                return false;
            }

            if (existingIdentity is null || string.IsNullOrWhiteSpace(existingIdentity.Guid))
            {
                error = "Shortcut identity file is invalid: guid is missing.";
                return false;
            }

            if (!Guid.TryParse(existingIdentity.Guid, out var parsedExistingGuid))
            {
                error = "Shortcut identity file is invalid: guid format is not valid.";
                return false;
            }

            var existingGuidNormalized = parsedExistingGuid.ToString("D");
            if (!string.IsNullOrWhiteSpace(manifestGuidNormalized)
                && !existingGuidNormalized.Equals(manifestGuidNormalized, StringComparison.OrdinalIgnoreCase))
            {
                error = "GUID conflict: manifest shortcut.guid differs from shortcut-identity.json on SD card.";
                return false;
            }

            shortcutGuid = existingGuidNormalized;
        }

        if (string.IsNullOrWhiteSpace(shortcutGuid))
        {
            shortcutGuid = !string.IsNullOrWhiteSpace(manifestGuidNormalized)
                ? manifestGuidNormalized
                : Guid.NewGuid().ToString("D");
        }

        var now = DateTimeOffset.UtcNow;
        var createdAt = existingIdentity?.CreatedAt ?? now;

        var identity = new ShortcutIdentity
        {
            SchemaVersion = 1,
            Guid = shortcutGuid,
            AppName = appName,
            CreatedAt = createdAt,
            UpdatedAt = now
        };

        try
        {
            fileSystem.CreateDirectory(Path.GetDirectoryName(identityPath)!);
            var json = JsonSerializer.Serialize(identity, new JsonSerializerOptions
            {
                PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
                WriteIndented = true
            });

            var tempPath = identityPath + ".tmp";
            fileSystem.WriteAllText(tempPath, json);
            fileSystem.MoveFile(tempPath, identityPath, overwrite: true);
        }
        catch (Exception ex)
        {
            error = $"Failed to write shortcut identity file: {ex.Message}";
            return false;
        }

        return true;
    }

    public bool TryRead(string sdRoot, out string shortcutGuid, out string error)
    {
        shortcutGuid = string.Empty;
        error = string.Empty;

        var identityPaths = new[]
        {
            Path.Combine(sdRoot, "shortcut-identity.json"),
            Path.Combine(sdRoot, "insertplay", "shortcut-identity.json")
        };

        foreach (var identityPath in identityPaths)
        {
            if (!fileSystem.FileExists(identityPath))
                continue;

            if (!TryParseGuidField(identityPath, out shortcutGuid, out var parseError))
            {
                error = $"Failed to read shortcut identity file '{identityPath}': {parseError}";
                return false;
            }

            return true;
        }

        return false;
    }

    private bool TryParseGuidField(string identityPath, out string shortcutGuid, out string error)
    {
        shortcutGuid = string.Empty;
        error = string.Empty;

        try
        {
            using var document = JsonDocument.Parse(fileSystem.ReadAllText(identityPath));
            if (!document.RootElement.TryGetProperty("guid", out var guidElement))
            {
                error = "guid field is missing.";
                return false;
            }

            var rawGuid = guidElement.GetString();
            if (string.IsNullOrWhiteSpace(rawGuid) || !Guid.TryParse(rawGuid, out var parsedGuid))
            {
                error = "guid field is invalid.";
                return false;
            }

            shortcutGuid = parsedGuid.ToString("D");
            return true;
        }
        catch (Exception ex)
        {
            error = ex.Message;
            return false;
        }
    }
}
