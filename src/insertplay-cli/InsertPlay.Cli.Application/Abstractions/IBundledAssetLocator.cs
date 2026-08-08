namespace InsertPlay.Cli.Application.Abstractions;

/// <summary>
/// Resolves files bundled alongside the running executable (scripts, artwork)
/// so use cases never depend on AppContext.BaseDirectory directly.
/// </summary>
public interface IBundledAssetLocator
{
    string BaseDirectory { get; }

    string? ResolveOutputFilePath(params string[] candidates);
}
