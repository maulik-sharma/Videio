const MAX_CONCURRENT_UPLOADS = 3;

function uploadPart(url, blob, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('PUT', url);
    request.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) onProgress(event.loaded);
    });
    request.addEventListener('error', () => reject(new Error('A storage upload failed.')));
    request.addEventListener('abort', () => reject(new Error('The upload was cancelled.')));
    request.addEventListener('load', () => {
      if (request.status < 200 || request.status >= 300) {
        reject(new Error(`Storage rejected an upload part (HTTP ${request.status}).`));
        return;
      }
      const etag = request.getResponseHeader('ETag');
      if (!etag) {
        reject(new Error('Storage did not return an ETag for an uploaded part.'));
        return;
      }
      resolve(etag);
    });
    request.send(blob);
  });
}

/** Upload presigned parts with at most three active requests and byte progress. */
export async function uploadParts(file, parts, onProgress) {
  const uploadedBytes = new Array(parts.length).fill(0);
  const result = new Array(parts.length);
  let nextIndex = 0;

  const report = () => {
    const uploaded = uploadedBytes.reduce((sum, value) => sum + value, 0);
    onProgress(Math.min(100, Math.round((uploaded / file.size) * 100)));
  };

  async function worker() {
    while (nextIndex < parts.length) {
      const index = nextIndex++;
      const part = parts[index];
      const start =
        index === 0 ? 0 : parts.slice(0, index).reduce((sum, item) => sum + item.size, 0);
      const blob = file.slice(start, start + part.size);
      result[index] = {
        part_number: part.part_number,
        etag: await uploadPart(part.url, blob, (loaded) => {
          uploadedBytes[index] = loaded;
          report();
        }),
      };
      uploadedBytes[index] = blob.size;
      report();
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(MAX_CONCURRENT_UPLOADS, parts.length) }, () => worker()),
  );
  return result;
}

export function partSizeFor(file, partSize) {
  return Array.from({ length: Math.ceil(file.size / partSize) }, (_, index) => {
    const start = index * partSize;
    return {
      part_number: index + 1,
      size: Math.min(partSize, file.size - start),
    };
  });
}
