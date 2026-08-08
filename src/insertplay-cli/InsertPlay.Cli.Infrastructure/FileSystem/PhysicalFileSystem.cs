using InsertPlay.Cli.Application.Abstractions;

namespace InsertPlay.Cli.Infrastructure.FileSystem;

public sealed class PhysicalFileSystem : IFileSystem
{
    public bool FileExists(string path) => File.Exists(path);

    public bool DirectoryExists(string path) => Directory.Exists(path);

    public void CreateDirectory(string path) => Directory.CreateDirectory(path);

    public void CopyFile(string sourcePath, string destinationPath, bool overwrite) =>
        File.Copy(sourcePath, destinationPath, overwrite);

    public void DeleteFile(string path) => File.Delete(path);

    public void MoveFile(string sourcePath, string destinationPath, bool overwrite) =>
        File.Move(sourcePath, destinationPath, overwrite);

    public string ReadAllText(string path) => File.ReadAllText(path);

    public void WriteAllText(string path, string contents) => File.WriteAllText(path, contents);

    public string[] ReadAllLines(string path) => File.ReadAllLines(path);

    public void WriteAllLines(string path, IEnumerable<string> lines) => File.WriteAllLines(path, lines);

    public string[] GetDirectories(string path) => Directory.GetDirectories(path);

    public string GetFullPath(string path) => Path.GetFullPath(path);
}
