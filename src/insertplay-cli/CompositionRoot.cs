using InsertPlay.Cli.Application.Abstractions;
using InsertPlay.Cli.Application.Services;
using InsertPlay.Cli.Application.UseCases.Configure;
using InsertPlay.Cli.Application.UseCases.Runtime;
using InsertPlay.Cli.Application.UseCases.Setup;
using InsertPlay.Cli.Application.UseCases.Shortcuts;
using InsertPlay.Cli.Application.UseCases.Steam;
using InsertPlay.Cli.Application.UseCases.Users;
using InsertPlay.Cli.Commands;
using InsertPlay.Cli.Infrastructure.Assets;
using InsertPlay.Cli.Infrastructure.Config;
using InsertPlay.Cli.Infrastructure.Identity;
using InsertPlay.Cli.Infrastructure.Manifest;
using InsertPlay.Cli.Infrastructure.Processes;
using InsertPlay.Cli.Infrastructure.Steam;
using Microsoft.Extensions.DependencyInjection;

namespace InsertPlay.Cli;

internal static class CompositionRoot
{
    public static ServiceProvider Build()
    {
        var services = new ServiceCollection();

        // Infrastructure (implements Application's abstractions)
        services.AddSingleton<IFileSystem, Infrastructure.FileSystem.PhysicalFileSystem>();
        services.AddSingleton<ISteamInstallationLocator, SteamInstallationLocator>();
        services.AddSingleton<IShortcutRepository, VdfShortcutRepository>();
        services.AddSingleton<IShortcutIdentityStore, FileShortcutIdentityStore>();
        services.AddSingleton<IEnvironmentFileStore, EnvironmentFileStore>();
        services.AddSingleton<IConfigStore, JsonConfigStore>();
        services.AddSingleton<IShortcutManifestRepository, JsonShortcutManifestRepository>();
        services.AddSingleton<IProcessRunner, SystemProcessRunner>();
        services.AddSingleton<IProcessMonitor, ProcessMonitor>();
        services.AddSingleton<ISteamProcessController, SteamProcessController>();
        services.AddSingleton<IBundledAssetLocator, BundledAssetLocator>();

        // Application services shared across use cases
        services.AddSingleton<SteamArtworkInstaller>();
        services.AddSingleton<RuntimeEnvironmentWriter>();

        // Application use cases
        services.AddSingleton<SetupUseCase>();
        services.AddSingleton<ListUsersUseCase>();
        services.AddSingleton<ConfigureUseCase>();
        services.AddSingleton<AddOrUpdateShortcutUseCase>();
        services.AddSingleton<RemoveShortcutUseCase>();
        services.AddSingleton<ListShortcutsUseCase>();
        services.AddSingleton<RestartSteamUseCase>();
        services.AddSingleton<InstallSdShortcutUseCase>();
        services.AddSingleton<AutorunLaunchUseCase>();
        services.AddSingleton<EnsureShortcutIdentityUseCase>();

        // Presentation
        services.AddSingleton<SetupCommand>();
        services.AddSingleton<UsersCommand>();
        services.AddSingleton<ConfigureCommand>();
        services.AddSingleton<ShortcutCommand>();
        services.AddSingleton<SteamCommand>();
        services.AddSingleton<RuntimeCommand>();
        services.AddSingleton<CommandRouter>();

        return services.BuildServiceProvider();
    }
}
