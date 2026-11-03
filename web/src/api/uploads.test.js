import { describe, expect, it } from 'vitest';
import { partSizeFor } from './uploads.js';

describe('partSizeFor', () => {
  it('splits a file into fixed-size parts with a smaller final part', () => {
    const file = { size: 25 };
    expect(partSizeFor(file, 10)).toEqual([
      { part_number: 1, size: 10 },
      { part_number: 2, size: 10 },
      { part_number: 3, size: 5 },
    ]);
  });

  it('returns one part for a file smaller than the part size', () => {
    expect(partSizeFor({ size: 4 }, 10)).toEqual([{ part_number: 1, size: 4 }]);
  });
});
