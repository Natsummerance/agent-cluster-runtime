import { useEffect } from 'react';
import type { CSSProperties } from 'react';
import { Select } from 'antd';
import { useAppStore } from '../store/appStore';
import { useIntl } from '../i18n';

interface ProjectSelectorProps {
  value?: string;
  onChange: (projectId: string) => void;
  placeholder?: string;
  style?: CSSProperties;
}

export default function ProjectSelector({
  value,
  onChange,
  placeholder,
  style,
}: ProjectSelectorProps) {
  const intl = useIntl();
  const projects = useAppStore((s) => s.projects);
  const refreshProjects = useAppStore((s) => s.refreshProjects);

  useEffect(() => {
    if (projects.length === 0) void refreshProjects();
  }, [projects.length, refreshProjects]);

  return (
    <Select
      showSearch
      placeholder={
        placeholder ??
        intl.formatMessage({ id: 'common.selectProject', defaultMessage: 'Select project' })
      }
      style={{ minWidth: 260, ...style }}
      value={value}
      onChange={onChange}
      options={projects.map((p) => ({
        value: p.id,
        label: intl.formatMessage(
          { id: 'projectSelector.option', defaultMessage: '{name} ({id})' },
          { name: p.name, id: p.id },
        ),
      }))}
      optionFilterProp="label"
      data-testid="project-selector"
    />
  );
}
