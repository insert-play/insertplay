namespace InsertPlay.Cli.Domain.Steam;

public sealed record SteamUserInfo(
    string UserId,
    string AccountName,
    string PersonaName,
    bool MostRecent);
