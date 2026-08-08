namespace InsertPlay.Cli.Domain.Steam;

public sealed class ShortcutIdentity
{
    public int SchemaVersion { get; set; }
    public string Guid { get; set; } = string.Empty;
    public string AppName { get; set; } = string.Empty;
    public DateTimeOffset CreatedAt { get; set; }
    public DateTimeOffset UpdatedAt { get; set; }
}
