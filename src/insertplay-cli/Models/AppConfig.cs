namespace InsertPlay.Cli.Models;

public sealed class AppConfig
{
    public int SchemaVersion { get; set; } = 1;
    public string SteamUserId { get; set; } = "";
    public string SteamPath { get; set; } = "";
    public DateTimeOffset CreatedAt { get; set; }
    public DateTimeOffset UpdatedAt { get; set; }
}
