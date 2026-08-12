import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Card,
  Form,
  Input,
  message,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { useAppStore } from '../store/appStore';
import { apiErrorMessage } from '../store/appStore';
import PageHeader from '../components/PageHeader';
import type { Project } from '../api/types';

export default function Projects() {
  const projects = useAppStore((s) => s.projects);
  const refreshProjects = useAppStore((s) => s.refreshProjects);
  const createProject = useAppStore((s) => s.createProject);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    void refreshProjects();
  }, [refreshProjects]);

  const handleCreate = useCallback(async () => {
    try {
      const values = await form.validateFields();
      setCreating(true);
      await createProject({ name: values.name.trim(), workspace: values.workspace.trim() });
      message.success(`项目「${values.name}」创建成功`);
      setModalOpen(false);
      form.resetFields();
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return; // 表单校验失败
      message.error(apiErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }, [form, createProject]);

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: 'ID', dataIndex: 'id', key: 'id', render: (v: string) => <Typography.Text className="mono">{v}</Typography.Text> },
    { title: '工作区', dataIndex: 'workspace', key: 'workspace' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (v: string) => <Tag color={v === 'active' ? 'green' : 'default'}>{v}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (v?: string) => (v ? String(v).slice(0, 19) : '-'),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: Project) => (
        <Space>
          <Link to={`/projects/${record.id}/sessions`}>
            <Button size="small" type="primary">会话</Button>
          </Link>
          <Link to={`/artifacts?project_id=${record.id}`}>
            <Button size="small">工作区</Button>
          </Link>
          <Link to={`/memory?project_id=${record.id}`}>
            <Button size="small">记忆</Button>
          </Link>
          <Link to={`/evolution?project_id=${record.id}`}>
            <Button size="small">进化</Button>
          </Link>
        </Space>
      ),
    },
  ];

  return (
    <div data-testid="projects-page">
      <PageHeader
        title="项目管理"
        description="创建项目并进入会话、工作区、记忆与进化"
        actions={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)} data-testid="create-project-btn">
            新建项目
          </Button>
        }
      />
      <Card>
        <Table<Project>
          rowKey="id"
          columns={columns}
          dataSource={projects}
          pagination={false}
          locale={{ emptyText: '暂无项目，点击右上角新建' }}
          data-testid="projects-table"
        />
      </Card>
      <Modal
        title="新建项目"
        open={modalOpen}
        onOk={handleCreate}
        confirmLoading={creating}
        onCancel={() => setModalOpen(false)}
        okText="创建"
        cancelText="取消"
        destroyOnClose
        data-testid="create-project-modal"
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入项目名称' }]}>
            <Input placeholder="例如：待办事项 Web 应用" data-testid="project-name-input" />
          </Form.Item>
          <Form.Item
            name="workspace"
            label="工作区路径"
            rules={[{ required: true, message: '请输入工作区路径' }]}
          >
            <Input placeholder="例如：.agent-cluster-demo/ws-todo" data-testid="project-workspace-input" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}