namespace InsertPlay.Cli.Models;

public sealed record SteamUserInfo(
    string UserId,
    string AccountName,
    string PersonaName,
    bool MostRecent);
