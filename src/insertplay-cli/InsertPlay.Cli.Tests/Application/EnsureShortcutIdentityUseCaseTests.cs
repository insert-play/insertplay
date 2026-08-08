using InsertPlay.Cli.Application.UseCases.Runtime;
using InsertPlay.Cli.Domain.Steam;
using InsertPlay.Cli.Tests.TestDoubles;

namespace InsertPlay.Cli.Tests.Application;

public class EnsureShortcutIdentityUseCaseTests
{
    [Fact]
    public void Execute_ReturnsUsageError_WhenSdRootMissing()
    {
        var useCase = new EnsureShortcutIdentityUseCase(
            new FakeFileSystem(), new FakeShortcutManifestRepository(), new FakeShortcutIdentityStore());

        var result = useCase.Execute(null);

        Assert.False(result.Success);
        Assert.Equal(1, result.ExitCode);
    }

    [Fact]
    public void Execute_ReturnsError_WhenSdRootDoesNotExist()
    {
        var useCase = new EnsureShortcutIdentityUseCase(
            new FakeFileSystem(), new FakeShortcutManifestRepository(), new FakeShortcutIdentityStore());

        var result = useCase.Execute("/sd");

        Assert.False(result.Success);
        Assert.Equal(1, result.ExitCode);
    }

    [Fact]
    public void Execute_UsesDefaultAppName_WhenNoManifestIsPresent()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var identityStore = new FakeShortcutIdentityStore { GetOrCreateResult = true, GetOrCreateGuid = "abc" };

        var useCase = new EnsureShortcutIdentityUseCase(fileSystem, new FakeShortcutManifestRepository { Result = false }, identityStore);
        var result = useCase.Execute("/sd");

        Assert.True(result.Success);
        Assert.Equal("abc", result.ShortcutGuid);
        Assert.Equal(Path.Combine("/sd", "shortcut-identity.json"), result.IdentityPath);
    }

    [Fact]
    public void Execute_UsesManifestAppNameAndGuid_WhenManifestIsValid()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        fileSystem.AddFile("/sd/steam-shortcut.json", "{}");

        var manifest = new SteamShortcutManifest
        {
            SchemaVersion = 1,
            Shortcut = new SteamShortcutDefinition { AppName = "My Game", Guid = "11111111-1111-1111-1111-111111111111" }
        };
        var manifestRepository = new FakeShortcutManifestRepository { Result = true, Manifest = manifest };
        var identityStore = new FakeShortcutIdentityStore { GetOrCreateResult = true, GetOrCreateGuid = "resolved-guid" };

        var useCase = new EnsureShortcutIdentityUseCase(fileSystem, manifestRepository, identityStore);
        var result = useCase.Execute("/sd");

        Assert.True(result.Success);
        Assert.Equal("resolved-guid", result.ShortcutGuid);
    }

    [Fact]
    public void Execute_ReturnsExitCode12_WhenIdentityStoreFails()
    {
        var fileSystem = new FakeFileSystem();
        fileSystem.AddDirectory("/sd");
        var identityStore = new FakeShortcutIdentityStore { GetOrCreateResult = false, GetOrCreateError = "conflict" };

        var useCase = new EnsureShortcutIdentityUseCase(fileSystem, new FakeShortcutManifestRepository { Result = false }, identityStore);
        var result = useCase.Execute("/sd");

        Assert.False(result.Success);
        Assert.Equal(12, result.ExitCode);
        Assert.Equal("conflict", result.ErrorMessage);
    }
}
