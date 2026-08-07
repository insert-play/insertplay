namespace InsertPlay.Cli.Models;

public sealed class SteamShortcutManifest
{
    public int SchemaVersion { get; set; }
    public SteamShortcutDefinition? Shortcut { get; set; }
}

public sealed class SteamShortcutDefinition
{
    public string? Guid { get; set; }
    public string AppName { get; set; } = string.Empty;
    public SteamShortcutVisibility? Visibility { get; set; }
    public SteamShortcutLaunch? Launch { get; set; }
    public SteamShortcutLaunchVerification? LaunchVerification { get; set; }
    public SteamShortcutArtwork? Artwork { get; set; }
}

public sealed class SteamShortcutVisibility
{
    public bool Hidden { get; set; }
}

public sealed class SteamShortcutLaunch
{
    public string Exe { get; set; } = string.Empty;
    public string Args { get; set; } = string.Empty;
    public string StartDir { get; set; } = string.Empty;
}

public sealed class SteamShortcutLaunchVerification
{
    public string ProcessName { get; set; } = string.Empty;
    public int TimeoutSeconds { get; set; }
}

public sealed class SteamShortcutArtwork
{
    public string? Icon { get; set; }
    public string? Capsule { get; set; }
    public string? Horizontal { get; set; }
    public string? Hero { get; set; }
    public string? Logo { get; set; }
}
