using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Application.Services;

/// <summary>
/// Pure validation rules for a deserialized shortcut manifest. Extracted from the JSON-reading code
/// so the business rules can be unit tested without touching the filesystem.
/// </summary>
public static class SteamShortcutManifestValidator
{
    public static bool Validate(SteamShortcutManifest? manifest, out string error)
    {
        error = string.Empty;

        if (manifest is null)
        {
            error = "Manifest is empty.";
            return false;
        }

        if (manifest.SchemaVersion != 1)
        {
            error = $"Unsupported manifest schemaVersion: {manifest.SchemaVersion}";
            return false;
        }

        if (manifest.Shortcut is null)
        {
            error = "Manifest field 'shortcut' is required.";
            return false;
        }

        if (string.IsNullOrWhiteSpace(manifest.Shortcut.AppName))
        {
            error = "Manifest field 'shortcut.appName' is required.";
            return false;
        }

        if (!string.IsNullOrWhiteSpace(manifest.Shortcut.Guid)
            && !Guid.TryParse(manifest.Shortcut.Guid, out _))
        {
            error = "Manifest field 'shortcut.guid' must be a valid UUID/GUID when provided.";
            return false;
        }

        if (manifest.Shortcut.Launch is null)
        {
            error = "Manifest field 'shortcut.launch' is required.";
            return false;
        }

        if (string.IsNullOrWhiteSpace(manifest.Shortcut.Launch.Exe))
        {
            error = "Manifest field 'shortcut.launch.exe' is required.";
            return false;
        }

        if (string.IsNullOrWhiteSpace(manifest.Shortcut.Launch.Args))
        {
            error = "Manifest field 'shortcut.launch.args' is required.";
            return false;
        }

        if (string.IsNullOrWhiteSpace(manifest.Shortcut.Launch.StartDir))
        {
            error = "Manifest field 'shortcut.launch.startDir' is required.";
            return false;
        }

        if (manifest.Shortcut.LaunchVerification is not null)
        {
            if (string.IsNullOrWhiteSpace(manifest.Shortcut.LaunchVerification.ProcessName))
            {
                error = "Manifest field 'shortcut.launchVerification.processName' is required.";
                return false;
            }

            if (manifest.Shortcut.LaunchVerification.TimeoutSeconds <= 0)
            {
                error = "Manifest field 'shortcut.launchVerification.timeoutSeconds' must be a positive integer.";
                return false;
            }
        }

        return true;
    }
}
