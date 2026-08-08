using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Domain.Steam;

namespace InsertPlay.Cli.Tests.TestDoubles;

internal sealed class FakeSteamInstallationLocator : ISteamInstallationLocator
{
    public string? Root { get; set; }
    public List<SteamUserInfo> Users { get; } = [];

    public string? FindRoot() => Root;

    public IReadOnlyList<SteamUserInfo> ListUsers(string steamRoot) => Users;
}
