using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Application.Abstractions;

public interface ISteamInstallationLocator
{
    string? FindRoot();
    IReadOnlyList<SteamUserInfo> ListUsers(string steamRoot);
}
