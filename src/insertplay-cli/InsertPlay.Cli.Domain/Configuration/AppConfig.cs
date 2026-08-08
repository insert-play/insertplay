namespace InsertPlay.Cli.Domain.Configuration;

public sealed class AppConfig
{
    public int SchemaVersion { get; set; } = 1;
    public string SteamUserId { get; set; } = "";
    public string SteamPath { get; set; } = "";
    public DateTimeOffset CreatedAt { get; set; }
    public DateTimeOffset UpdatedAt { get; set; }
}
