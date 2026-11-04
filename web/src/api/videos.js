import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client.js';

const MY_VIDEOS = ['my-videos'];
const FEED = ['feed'];
const IN_FLIGHT = new Set(['uploaded', 'processing']);

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
    // Poll while anything is still on its way to ready or failed.
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) =>
        page.items.some((video) => IN_FLIGHT.has(video.status)),
      )
        ? 3000
        : false,
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: MY_VIDEOS });
      queryClient.invalidateQueries({ queryKey: FEED });
    },
  });
}

export function useFeed() {
  return useInfiniteQuery({
    queryKey: FEED,
    queryFn: ({ pageParam }) => {
      const query = new URLSearchParams({ limit: '20' });
      if (pageParam) query.set('cursor', pageParam);
      return api(`/api/feed?${query}`);
    },
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    retry: false,
  });
}

export function useVideo(publicId) {
  return useQuery({
    queryKey: ['video', publicId],
    queryFn: () => api(`/api/videos/${encodeURIComponent(publicId)}`),
    enabled: Boolean(publicId),
    retry: false,
    refetchInterval: (query) => {
      if (query.state.status === 'error') return false;
      const status = query.state.data?.status;
      return status === 'ready' || status === 'failed' ? false : 3000;
    },
  });
}

export function useUpdateVideo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ publicId, changes }) =>
      api(`/api/videos/${encodeURIComponent(publicId)}`, { method: 'PATCH', body: changes }),
    onSuccess: (video) => {
      queryClient.setQueryData(['video', video.public_id], video);
      queryClient.invalidateQueries({ queryKey: MY_VIDEOS });
      queryClient.invalidateQueries({ queryKey: FEED });
    },
  });
}

export function useDeleteVideo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (publicId) =>
      api(`/api/videos/${encodeURIComponent(publicId)}`, { method: 'DELETE' }),
    onSuccess: (_, publicId) => {
      queryClient.removeQueries({ queryKey: ['video', publicId] });
      queryClient.invalidateQueries({ queryKey: MY_VIDEOS });
      queryClient.invalidateQueries({ queryKey: FEED });
    },
  });
}

export function useCountVideoView() {
  return useMutation({
    mutationFn: (publicId) =>
      api(`/api/videos/${encodeURIComponent(publicId)}/view`, { method: 'POST' }),
  });
}
