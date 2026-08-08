using InsertPlay.Cli.Application.UseCases.Steam;
using InsertPlay.Cli.Domain.Configuration;
using InsertPlay.Cli.Tests.TestDoubles;

namespace InsertPlay.Cli.Tests.Application;

public class RestartSteamUseCaseTests
{
    [Fact]
    public void Execute_ReturnsError_WhenSteamPathCannotBeResolved()
    {
        var useCase = new RestartSteamUseCase(
            new FakeConfigStore(), new FakeSteamInstallationLocator(), new FakeFileSystem(), new FakeSteamProcessController());

        var result = useCase.Execute();

        Assert.False(result.Success);
        Assert.Equal(2, result.ExitCode);
    }

    [Fact]
    public void Execute_ReturnsExitCode20_WhenControllerFailsToRestart()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/steam");
        var configStore = new FakeConfigStore { Config = new AppConfig { SteamPath = "/steam" } };
        var controller = new FakeSteamProcessController { RestartResult = false, RestartMessage = "no process manager" };

        var useCase = new RestartSteamUseCase(configStore, new FakeSteamInstallationLocator(), fileSystem, controller);
        var result = useCase.Execute();

        Assert.False(result.Success);
        Assert.Equal(20, result.ExitCode);
        Assert.Equal("Steam restart failed: no process manager", result.Message);
    }

    [Fact]
    public void Execute_Succeeds_WhenControllerRestartsSteam()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/steam");
        var configStore = new FakeConfigStore { Config = new AppConfig { SteamPath = "/steam" } };
        var controller = new FakeSteamProcessController { RestartResult = true, RestartMessage = "restarted" };

        var useCase = new RestartSteamUseCase(configStore, new FakeSteamInstallationLocator(), fileSystem, controller);
        var result = useCase.Execute();

        Assert.True(result.Success);
        Assert.Equal(0, result.ExitCode);
        Assert.Equal("restarted", result.Message);
    }
}
