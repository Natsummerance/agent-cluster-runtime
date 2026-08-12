// 统一页面头部：标题 + 描述 + 右侧操作（见 DESIGN.md §4 布局一致性）
import type { ReactNode } from 'react';
import { Space, Typography } from 'antd';

interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  testId?: string;
}

export default function PageHeader({ title, description, actions, testId }: PageHeaderProps) {
  return (
    <div className="page-header" data-testid={testId ?? 'page-header'}>
      <div className="page-header__text">
        <Typography.Title level={3} className="page-header__title">
          {title}
        </Typography.Title>
        {description && (
          <Typography.Paragraph type="secondary" className="page-header__desc">
            {description}
          </Typography.Paragraph>
        )}
      </div>
      {actions && (
        <Space wrap className="page-header__actions">
          {actions}
        </Space>
      )}
    </div>
  );
}