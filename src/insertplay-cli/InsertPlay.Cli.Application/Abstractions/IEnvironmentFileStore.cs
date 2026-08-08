namespace InsertPlay.Cli.Application.Abstractions;

public interface IEnvironmentFileStore
{
    void Write(string path, IReadOnlyDictionary<string, string> variables);

    bool TryRead(string path, out Dictionary<string, string> variables, out string error);
}
