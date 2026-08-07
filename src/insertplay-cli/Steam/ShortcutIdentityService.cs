using System.Text.Json;

namespace InsertPlay.Cli.Steam;

public sealed class ShortcutIdentityService
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

        ShortcutIdentityFile? existingIdentity = null;
        var existingIdentityPath = string.Empty;
        if (File.Exists(identityPath))
            existingIdentityPath = identityPath;
        else if (File.Exists(legacyIdentityPath))
            existingIdentityPath = legacyIdentityPath;

        if (!string.IsNullOrWhiteSpace(existingIdentityPath))
        {
            try
            {
                var existingJson = File.ReadAllText(existingIdentityPath);
                existingIdentity = JsonSerializer.Deserialize<ShortcutIdentityFile>(existingJson, new JsonSerializerOptions
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
            if (!string.IsNullOrWhiteSpace(manifestGuidNormalized))
                shortcutGuid = manifestGuidNormalized;
            else
                shortcutGuid = Guid.NewGuid().ToString("D");
        }

        var now = DateTimeOffset.UtcNow;
        var createdAt = existingIdentity?.CreatedAt ?? now;

        var identity = new ShortcutIdentityFile
        {
            SchemaVersion = 1,
            Guid = shortcutGuid,
            AppName = appName,
            CreatedAt = createdAt,
            UpdatedAt = now
        };

        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(identityPath)!);
            var json = JsonSerializer.Serialize(identity, new JsonSerializerOptions
            {
                PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
                WriteIndented = true
            });

            var tempPath = identityPath + ".tmp";
            File.WriteAllText(tempPath, json);
            File.Move(tempPath, identityPath, overwrite: true);
        }
        catch (Exception ex)
        {
            error = $"Failed to write shortcut identity file: {ex.Message}";
            return false;
        }

        return true;
    }
}

public sealed class ShortcutIdentityFile
{
    public int SchemaVersion { get; set; }
    public string Guid { get; set; } = string.Empty;
    public string AppName { get; set; } = string.Empty;
    public DateTimeOffset CreatedAt { get; set; }
    public DateTimeOffset UpdatedAt { get; set; }
}
