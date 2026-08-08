using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Tests.TestDoubles;

internal sealed class FakeEnvironmentFileStore : IEnvironmentFileStore
{
    public Dictionary<string, Dictionary<string, string>> Written { get; } = new();
    public Dictionary<string, (Dictionary<string, string> Variables, string Error)> ReadResults { get; } = new();

    public void Write(string path, IReadOnlyDictionary<string, string> variables) =>
        Written[path] = new Dictionary<string, string>(variables);

    public bool TryRead(string path, out Dictionary<string, string> variables, out string error)
    {
        if (ReadResults.TryGetValue(path, out var result))
        {
            variables = result.Variables;
            error = result.Error;
            return string.IsNullOrEmpty(error);
        }

        variables = new Dictionary<string, string>();
        error = $"Environment file not found: {path}";
        return false;
    }
}
