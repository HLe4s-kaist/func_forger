#include <stdio.h>

// Reads raw bytes from a file starting at a specified offset.
//
// Arguments:
// file_name (void *): A pointer to a null-terminated string containing the path to the target file.
// idx (int): The byte offset from the start of the file where reading should begin.
// buf (char *): A pointer to the destination buffer where the read bytes will be stored.
// len (int): The maximum number of bytes to be read from the file.
//
// Returns:
// The number of bytes successfully read from the file.
//
// Behavior:
// 1. The `file_name` pointer is cast to a `const char *` to access the file path string.
// 2. The file is opened in binary read mode ("rb") using the standard `fopen` function.
// 3. If the file cannot be opened, the function returns 0.
// 4. The file pointer is moved to the position specified by `idx` from the start of the file using `fseek` with the `SEEK_SET` flag.
// 5. If the seek operation fails, the file is closed and the function returns 0.
// 6. Up to `len` bytes are read from the current file position into `buf` using the `fread` function.
// 7. The file is closed using `fclose`.
// 8. The number of bytes actually read by `fread` is returned as an integer.
int read_raw_byte_from_file(void * file_name, int idx, char * buf, int len)
{
    if (file_name == NULL || buf == NULL || len <= 0) {
        return 0;
    }

    // Open the file in binary read mode
    FILE *file = fopen((const char *)file_name, "rb");
    if (file == NULL) {
        return 0;
    }

    // Move the file pointer to the requested index from the start of the file
    // We cast idx to long to match the signature requirements of fseek
    if (fseek(file, (long)idx, SEEK_SET) != 0) {
        fclose(file);
        return 0;
    }

    // fread is used to read up to 'len' bytes. 
    // We treat each element as 1 byte to ensure the return value matches the count of bytes.
    size_t bytes_read = fread(buf, 1, (size_t)len, file);

    // Always close the file stream to prevent leaks
    fclose(file);

    return (int)bytes_read;
}