interface MockFileType extends Blob {
  readonly lastModified: number;
  readonly name: string;
}
export function MockFile(
  name: string,
  size: number,
  mimeType: string
): MockFileType {
  function range(count: number) {
    var output = "";
    for (var i = 0; i < count; i++) {
      output += "a";
    }
    return output;
  }

  var blob = {
    ...new Blob([range(size)], { type: mimeType }),
    lastModified: new Date().getSeconds(),
    name: name,
  } as MockFileType;
  return blob;
}
