import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from './client.js';

const MY_VIDEOS = ['my-videos'];

export function useMyVideos(enabled = true) {
  return useInfiniteQuery({
    queryKey: MY_VIDEOS,
    queryFn: ({ pageParam }) => {
      const query = new URLSearchParams({ limit: '20' });
      if (pageParam) query.set('cursor', pageParam);
      return api(`/api/me/videos?${query}`);
    },
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    retry: false,
    enabled,
  });
}

export function useCreateVideoUpload() {
  return useMutation({
    mutationFn: ({ title, description, visibility, file }) =>
      api('/api/videos', {
        method: 'POST',
        body: {
          title,
          description,
          visibility,
          size_bytes: file.size,
          content_type: file.type,
        },
      }),
  });
}

export function useCompleteVideoUpload() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ publicId, parts }) =>
      api(`/api/videos/${encodeURIComponent(publicId)}/complete`, {
        method: 'POST',
        body: { parts },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: MY_VIDEOS }),
  });
}
