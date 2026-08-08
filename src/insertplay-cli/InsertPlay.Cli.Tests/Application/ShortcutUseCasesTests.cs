using InsertPlay.Cli.Application.UseCases.Shortcuts;
using InsertPlay.Cli.Domain.Configuration;
using InsertPlay.Cli.Domain.Steam;
using InsertPlay.Cli.Tests.TestDoubles;

namespace InsertPlay.Cli.Tests.Application;

public class ShortcutUseCasesTests
{
    private static FakeConfigStore ValidConfigStore() => new()
    {
        Config = new AppConfig { SteamPath = "/steam", SteamUserId = "111", SchemaVersion = 1 }
    };

    [Fact]
    public void AddOrUpdate_ReturnsConfigError_WhenConfigIsInvalid()
    {
        var configStore = new FakeConfigStore { ValidResult = false, ValidError = "bad config" };
        var useCase = new AddOrUpdateShortcutUseCase(configStore, new FakeShortcutRepository());

        var result = useCase.Execute("Game", "/bin/game", "", "", "", false);

        Assert.False(result.Success);
        Assert.Equal(1, result.ExitCode);
        Assert.Equal("bad config", result.Message);
    }

    [Fact]
    public void AddOrUpdate_SavesShortcut_WhenConfigIsValid()
    {
        var repository = new FakeShortcutRepository();
        var useCase = new AddOrUpdateShortcutUseCase(ValidConfigStore(), repository);

        var result = useCase.Execute("Game", "/bin/game", "/dir", "--flag", "icon.png", true);

        Assert.True(result.Success);
        Assert.Equal(0, result.ExitCode);
        Assert.NotNull(repository.LastAddedOrUpdated);
        Assert.Equal("Game", repository.LastAddedOrUpdated!.AppName);
        Assert.True(repository.LastAddedOrUpdated.IsHidden);
    }

    [Fact]
    public void AddOrUpdate_ReturnsExitCode5_WhenRepositoryFails()
    {
        var repository = new FakeShortcutRepository { AddOrUpdateResult = false, AddOrUpdateMessage = "failed" };
        var useCase = new AddOrUpdateShortcutUseCase(ValidConfigStore(), repository);

        var result = useCase.Execute("Game", "/bin/game", "", "", "", false);

        Assert.False(result.Success);
        Assert.Equal(5, result.ExitCode);
    }

    [Fact]
    public void Remove_ReturnsExitCode6_WhenRepositoryFails()
    {
        var repository = new FakeShortcutRepository { RemoveResult = false, RemoveMessage = "Shortcut not found." };
        var useCase = new RemoveShortcutUseCase(ValidConfigStore(), repository);

        var result = useCase.Execute("Missing");

        Assert.False(result.Success);
        Assert.Equal(6, result.ExitCode);
        Assert.Equal("Shortcut not found.", result.Message);
    }

    [Fact]
    public void Remove_Succeeds_WhenRepositoryFindsIt()
    {
        var repository = new FakeShortcutRepository { RemoveResult = true, RemoveMessage = "Shortcut removed." };
        var useCase = new RemoveShortcutUseCase(ValidConfigStore(), repository);

        var result = useCase.Execute("Game");

        Assert.True(result.Success);
        Assert.Equal(0, result.ExitCode);
    }

    [Fact]
    public void List_ReturnsConfigError_WhenConfigIsInvalid()
    {
        var configStore = new FakeConfigStore { ValidResult = false, ValidError = "bad config" };
        var useCase = new ListShortcutsUseCase(configStore, new FakeShortcutRepository());

        var result = useCase.Execute();

        Assert.False(result.Success);
        Assert.Equal(1, result.ExitCode);
        Assert.Empty(result.Items);
    }

    [Fact]
    public void List_ReturnsShortcutsOrderedByName()
    {
        var repository = new FakeShortcutRepository();
        repository.Entries.Add(new SteamShortcutEntry { AppName = "Zelda" });
        repository.Entries.Add(new SteamShortcutEntry { AppName = "Aladdin" });

        var useCase = new ListShortcutsUseCase(ValidConfigStore(), repository);
        var result = useCase.Execute();

        Assert.True(result.Success);
        Assert.Equal(["Aladdin", "Zelda"], result.Items.Select(i => i.AppName));
    }
}
