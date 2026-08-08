using InsertPlay.Cli.Application.UseCases.Users;
using InsertPlay.Cli.Domain.Configuration;
using InsertPlay.Cli.Domain.Steam;
using InsertPlay.Cli.Tests.TestDoubles;

namespace InsertPlay.Cli.Tests.Application;

public class ListUsersUseCaseTests
{
    [Fact]
    public void Execute_UsesConfiguredSteamPath_WhenItExists()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/steam-from-config");
        var configStore = new FakeConfigStore { Config = new AppConfig { SteamPath = "/steam-from-config", SteamUserId = "111" } };
        var locator = new FakeSteamInstallationLocator { Root = "/should-not-be-used" };
        locator.Users.Add(new SteamUserInfo("111", "acct", "Persona", true));

        var useCase = new ListUsersUseCase(configStore, locator, fileSystem);
        var result = useCase.Execute();

        Assert.True(result.SteamFound);
        Assert.Equal("111", result.ConfiguredUserId);
        Assert.Single(result.Users);
    }

    [Fact]
    public void Execute_FallsBackToLocator_WhenConfiguredPathIsMissing()
    {
        var fileSystem = new FakeFileSystem();
        var configStore = new FakeConfigStore { Config = new AppConfig { SteamPath = "/gone", SteamUserId = "111" } };
        var locator = new FakeSteamInstallationLocator { Root = "/steam-found" };

        var useCase = new ListUsersUseCase(configStore, locator, fileSystem);
        var result = useCase.Execute();

        Assert.True(result.SteamFound);
    }

    [Fact]
    public void Execute_ReturnsError_WhenSteamCannotBeFoundAtAll()
    {
        var useCase = new ListUsersUseCase(new FakeConfigStore(), new FakeSteamInstallationLocator(), new FakeFileSystem());

        var result = useCase.Execute();

        Assert.False(result.SteamFound);
        Assert.Equal(2, result.ExitCode);
        Assert.Equal("Steam not found.", result.Error);
    }
}
