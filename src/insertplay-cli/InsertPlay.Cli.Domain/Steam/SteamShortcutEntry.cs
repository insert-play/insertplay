namespace InsertPlay.Cli.Domain.Steam;

public sealed class SteamShortcutEntry
{
    public uint AppId { get; set; }
    public string AppName { get; set; } = "";
    public string Exe { get; set; } = "";
    public string StartDir { get; set; } = "";
    public string Icon { get; set; } = "";
    public string ShortcutPath { get; set; } = "";
    public string LaunchOptions { get; set; } = "";
    public bool IsHidden { get; set; }
    public List<string> Tags { get; set; } = [];
}
