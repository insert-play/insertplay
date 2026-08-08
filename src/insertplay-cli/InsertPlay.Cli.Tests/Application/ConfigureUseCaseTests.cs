using InsertPlay.Cli.Application.Services;
using InsertPlay.Cli.Application.UseCases.Configure;
using InsertPlay.Cli.Domain.Steam;
using InsertPlay.Cli.Tests.TestDoubles;

namespace InsertPlay.Cli.Tests.Application;

public class ConfigureUseCaseTests
{
    private static ConfigureUseCase CreateUseCase(FakeConfigStore configStore, FakeSteamInstallationLocator locator, FakeFileSystem fileSystem) =>
        new(configStore, locator, fileSystem, new RuntimeEnvironmentWriter(configStore));

    [Fact]
    public void Execute_ReturnsError_WhenSteamNotFound()
    {
        var useCase = CreateUseCase(new FakeConfigStore(), new FakeSteamInstallationLocator(), new FakeFileSystem());

        var result = useCase.Execute("111");

        Assert.False(result.Success);
        Assert.Equal(2, result.ExitCode);
    }

    [Fact]
    public void Execute_ReturnsError_WhenUserIsNotAmongSteamUsers()
    {
        var locator = new FakeSteamInstallationLocator { Root = "/steam" };
        locator.Users.Add(new SteamUserInfo("999", "acct", "Persona", true));

        var useCase = CreateUseCase(new FakeConfigStore(), locator, new FakeFileSystem());
        var result = useCase.Execute("111");

        Assert.False(result.Success);
        Assert.Equal(3, result.ExitCode);
        Assert.Equal("The specified user was not found in userdata.", result.Error);
    }

    [Fact]
    public void Execute_SavesConfigAndRuntimeEnvironment_WhenUserIsValid()
    {
        var locator = new FakeSteamInstallationLocator { Root = "/steam" };
        locator.Users.Add(new SteamUserInfo("111", "acct", "Persona", true));
        var configStore = new FakeConfigStore();

        var useCase = CreateUseCase(configStore, locator, new FakeFileSystem());
        var result = useCase.Execute("111");

        Assert.True(result.Success);
        Assert.Equal("111", configStore.Config!.SteamUserId);
        Assert.Equal("/steam", configStore.Config.SteamPath);
        Assert.NotNull(configStore.LastSavedRuntimeEnvironment);
        Assert.Equal("/config/runtime.env", result.RuntimeEnvPath);
    }
}
