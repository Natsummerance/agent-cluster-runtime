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
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import type { Project } from '../api/types';

export default function Projects() {
  const intl = useIntl();
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
      message.success(
        intl.formatMessage(
          { id: 'projects.createSuccess', defaultMessage: 'Project "{name}" created' },
          { name: values.name },
        ),
      );
      setModalOpen(false);
      form.resetFields();
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) return; // 表单校验失败
      message.error(apiErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }, [form, createProject, intl]);

  const columns = [
    { title: intl.formatMessage({ id: 'common.name', defaultMessage: 'Name' }), dataIndex: 'name', key: 'name' },
    { title: 'ID', dataIndex: 'id', key: 'id', render: (v: string) => <Typography.Text className="mono">{v}</Typography.Text> },
    { title: intl.formatMessage({ id: 'common.workspace', defaultMessage: 'Workspace' }), dataIndex: 'workspace', key: 'workspace' },
    {
      title: intl.formatMessage({ id: 'common.status', defaultMessage: 'Status' }),
      dataIndex: 'status',
      key: 'status',
      render: (v: string) => <Tag color={v === 'active' ? 'green' : 'default'}>{v}</Tag>,
    },
    {
      title: intl.formatMessage({ id: 'common.createdAt', defaultMessage: 'Created at' }),
      dataIndex: 'created_at',
      key: 'created_at',
      render: (v?: string) => (v ? String(v).slice(0, 19) : '-'),
    },
    {
      title: intl.formatMessage({ id: 'common.actions', defaultMessage: 'Actions' }),
      key: 'actions',
      render: (_: unknown, record: Project) => (
        <Space>
          <Link to={`/projects/${record.id}/sessions`}>
            <Button size="small" type="primary">
              {intl.formatMessage({ id: 'common.session', defaultMessage: 'Sessions' })}
            </Button>
          </Link>
          <Link to={`/artifacts?project_id=${record.id}`}>
            <Button size="small">
              {intl.formatMessage({ id: 'common.workspace', defaultMessage: 'Workspace' })}
            </Button>
          </Link>
          <Link to={`/memory?project_id=${record.id}`}>
            <Button size="small">
              {intl.formatMessage({ id: 'common.memory', defaultMessage: 'Memory' })}
            </Button>
          </Link>
          <Link to={`/evolution?project_id=${record.id}`}>
            <Button size="small">
              {intl.formatMessage({ id: 'common.evolution', defaultMessage: 'Evolution' })}
            </Button>
          </Link>
        </Space>
      ),
    },
  ];

  return (
    <div data-testid="projects-page">
      <PageHeader
        title={intl.formatMessage({ id: 'projects.header.title', defaultMessage: 'Project management' })}
        description={intl.formatMessage({
          id: 'projects.header.desc',
          defaultMessage: 'Create projects and jump to sessions, workspace, memory and evolution',
        })}
        actions={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)} data-testid="create-project-btn">
            {intl.formatMessage({ id: 'projects.create', defaultMessage: 'New project' })}
          </Button>
        }
      />
      <Card>
        <Table<Project>
          rowKey="id"
          columns={columns}
          dataSource={projects}
          pagination={false}
          locale={{
            emptyText: intl.formatMessage({
              id: 'projects.empty',
              defaultMessage: 'No projects; click "New project" in the top right',
            }),
          }}
          data-testid="projects-table"
        />
      </Card>
      <Modal
        title={intl.formatMessage({ id: 'projects.modal.title', defaultMessage: 'New project' })}
        open={modalOpen}
        onOk={handleCreate}
        confirmLoading={creating}
        onCancel={() => setModalOpen(false)}
        okText={intl.formatMessage({ id: 'common.create', defaultMessage: 'Create' })}
        cancelText={intl.formatMessage({ id: 'common.cancel', defaultMessage: 'Cancel' })}
        destroyOnHidden
        data-testid="create-project-modal"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label={intl.formatMessage({ id: 'projects.modal.name', defaultMessage: 'Project name' })}
            rules={[
              {
                required: true,
                message: intl.formatMessage({
                  id: 'projects.modal.nameRequired',
                  defaultMessage: 'Please enter the project name',
                }),
              },
            ]}
          >
            <Input
              placeholder={intl.formatMessage({
                id: 'projects.modal.namePlaceholder',
                defaultMessage: 'e.g. Todo web app',
              })}
              data-testid="project-name-input"
            />
          </Form.Item>
          <Form.Item
            name="workspace"
            label={intl.formatMessage({ id: 'projects.modal.workspace', defaultMessage: 'Workspace path' })}
            rules={[
              {
                required: true,
                message: intl.formatMessage({
                  id: 'projects.modal.workspaceRequired',
                  defaultMessage: 'Please enter the workspace path',
                }),
              },
            ]}
          >
            <Input
              placeholder={intl.formatMessage({
                id: 'projects.modal.workspacePlaceholder',
                defaultMessage: 'e.g. .agent-cluster-demo/ws-todo',
              })}
              data-testid="project-workspace-input"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
