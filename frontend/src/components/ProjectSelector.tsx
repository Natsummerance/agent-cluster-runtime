import { useEffect } from 'react';
import type { CSSProperties } from 'react';
import { Select } from 'antd';
import { useAppStore } from '../store/appStore';

interface ProjectSelectorProps {
  value?: string;
  onChange: (projectId: string) => void;
  placeholder?: string;
  style?: CSSProperties;
}

export default function ProjectSelector({
  value,
  onChange,
  placeholder = '选择项目',
  style,
}: ProjectSelectorProps) {
  const projects = useAppStore((s) => s.projects);
  const refreshProjects = useAppStore((s) => s.refreshProjects);

  useEffect(() => {
    if (projects.length === 0) void refreshProjects();
  }, [projects.length, refreshProjects]);

  return (
    <Select
      showSearch
      placeholder={placeholder}
      style={{ minWidth: 260, ...style }}
      value={value}
      onChange={onChange}
      options={projects.map((p) => ({ value: p.id, label: `${p.name}（${p.id}）` }))}
      optionFilterProp="label"
      data-testid="project-selector"
    />
  );
}