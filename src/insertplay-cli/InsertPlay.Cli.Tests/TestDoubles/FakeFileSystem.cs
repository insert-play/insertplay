using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Tests.TestDoubles;

internal sealed class FakeFileSystem : IFileSystem
{
    private readonly Dictionary<string, string> _files = new();
    private readonly HashSet<string> _directories = new();

    public void AddDirectory(string path) => _directories.Add(path);

    public void AddFile(string path, string contents = "")
    {
        _files[path] = contents;
        var dir = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(dir))
            _directories.Add(dir);
    }

    public bool FileExists(string path) => _files.ContainsKey(path);

    public bool DirectoryExists(string path) => _directories.Contains(path);

    public void CreateDirectory(string path) => _directories.Add(path);

    public void CopyFile(string sourcePath, string destinationPath, bool overwrite)
    {
        if (!_files.TryGetValue(sourcePath, out var content))
            throw new FileNotFoundException(sourcePath);

        _files[destinationPath] = content;
    }

    public void DeleteFile(string path) => _files.Remove(path);

    public void MoveFile(string sourcePath, string destinationPath, bool overwrite)
    {
        if (!_files.TryGetValue(sourcePath, out var content))
            throw new FileNotFoundException(sourcePath);

        _files.Remove(sourcePath);
        _files[destinationPath] = content;
    }

    public string ReadAllText(string path) =>
        _files.TryGetValue(path, out var content) ? content : throw new FileNotFoundException(path);

    public void WriteAllText(string path, string contents) => AddFile(path, contents);

    public string[] ReadAllLines(string path) => ReadAllText(path).Split('\n');

    public void WriteAllLines(string path, IEnumerable<string> lines) => WriteAllText(path, string.Join('\n', lines));

    public string[] GetDirectories(string path) =>
        _directories.Where(d => Path.GetDirectoryName(d) == path).ToArray();

    public string GetFullPath(string path) => Path.IsPathRooted(path) ? path : "/" + path;
}
