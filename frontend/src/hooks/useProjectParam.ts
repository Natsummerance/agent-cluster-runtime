// 复用逻辑：项目参数读取/写回（react-next-baseline：参数读取下沉到 hooks）
import { useSearchParams } from 'react-router-dom';

export function useProjectParam(): [string, (projectId: string) => void] {
  const [searchParams, setSearchParams] = useSearchParams();
  const projectId = searchParams.get('project_id') ?? '';
  const setProjectId = (id: string) => setSearchParams(id ? { project_id: id } : {});
  return [projectId, setProjectId];
}

export function useSessionParam(): [string, (sessionId: string) => void] {
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionId = searchParams.get('session_id') ?? '';
  const setSessionId = (id: string) => setSearchParams(id ? { session_id: id } : {});
  return [sessionId, setSessionId];
}