import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, message } from 'antd';
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import type { RbacRole, RbacUser } from '../api/types';

export default function Users() {
  const intl = useIntl();
  const [users, setUsers] = useState<RbacUser[]>([]);
  const [roles, setRoles] = useState<RbacRole[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [userData, roleData] = await Promise.all([api.fetchUsers(), api.fetchRoles()]);
      setUsers(userData.users);
      setRoles(roleData.roles);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = useCallback(async () => {
    const values = await form.validateFields();
    setCreating(true);
    try {
      await api.createUser({
        id: values.id.trim(),
        name: values.name.trim(),
        role_ids: values.role_ids ?? [],
        scopes: values.scopes ? values.scopes.split(',').map((s: string) => s.trim()).filter(Boolean) : [],
      });
      message.success(intl.formatMessage({ id: 'users.created', defaultMessage: 'User created' }));
      setModalOpen(false);
      form.resetFields();
      void load();
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }, [form, load, intl]);

  const handleDelete = useCallback(
    async (userId: string) => {
      try {
        await api.deleteUser(userId);
        message.success(intl.formatMessage({ id: 'users.deleted', defaultMessage: 'User removed' }));
        void load();
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [load, intl],
  );

  const roleName = useMemo(() => {
    const map = new Map(roles.map((r) => [r.id, r.name]));
    return (ids: string[]) => ids.map((id) => map.get(id) ?? id);
  }, [roles]);

  const columns = useMemo(
    () => [
      { title: intl.formatMessage({ id: 'users.col.id', defaultMessage: 'ID' }), dataIndex: 'id', render: (v: string) => <span className="mono">{v}</span> },
      { title: intl.formatMessage({ id: 'users.col.name', defaultMessage: 'Name' }), dataIndex: 'name' },
      {
        title: intl.formatMessage({ id: 'users.col.roles', defaultMessage: 'Roles' }),
        dataIndex: 'role_ids',
        render: (ids: string[]) => (
          <Space size={4} wrap>
            {(ids ?? []).map((id) => (
              <Tag key={id} color="blue">
                {roleName([id])[0]}
              </Tag>
            ))}
          </Space>
        ),
      },
      {
        title: intl.formatMessage({ id: 'users.col.scopes', defaultMessage: 'Project scope' }),
        dataIndex: 'scopes',
        render: (scopes: string[]) => (scopes ?? []).join(', ') || '-',
      },
      {
        title: '',
        key: 'actions',
        render: (_: unknown, record: RbacUser) =>
          record.is_admin ? null : (
            <Popconfirm
              title={intl.formatMessage({ id: 'users.deleteConfirm', defaultMessage: 'Remove this user?' })}
              onConfirm={() => handleDelete(record.id)}
            >
              <Button size="small" danger icon={<DeleteOutlined />} data-testid={`delete-user-${record.id}`} />
            </Popconfirm>
          ),
      },
    ],
    [intl, roleName, handleDelete],
  );

  return (
    <div data-testid="users-page">
      <PageHeader
        title={intl.formatMessage({ id: 'users.header.title', defaultMessage: 'Users & Roles' })}
        description={intl.formatMessage({
          id: 'users.header.desc',
          defaultMessage: 'Permission matrix, role assignment and per-project scope',
        })}
        actions={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()} data-testid="refresh-users-btn" />
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)} data-testid="add-user-btn">
              {intl.formatMessage({ id: 'users.create', defaultMessage: 'New user' })}
            </Button>
          </Space>
        }
      />
      <Card>
        <Table<RbacUser>
          rowKey="id"
          columns={columns}
          dataSource={users}
          loading={loading}
          pagination={false}
          locale={{ emptyText: intl.formatMessage({ id: 'users.empty', defaultMessage: 'No users' }) }}
          data-testid="users-table"
        />
      </Card>
      <Modal
        title={intl.formatMessage({ id: 'users.modal.title', defaultMessage: 'New user' })}
        open={modalOpen}
        onOk={handleCreate}
        confirmLoading={creating}
        onCancel={() => setModalOpen(false)}
        okText={intl.formatMessage({ id: 'common.create', defaultMessage: 'Create' })}
        cancelText={intl.formatMessage({ id: 'common.cancel', defaultMessage: 'Cancel' })}
        destroyOnHidden
        data-testid="create-user-modal"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="id"
            label={intl.formatMessage({ id: 'users.modal.id', defaultMessage: 'User ID' })}
            rules={[{ required: true, message: intl.formatMessage({ id: 'users.modal.idRequired', defaultMessage: 'Please enter the user ID' }) }]}
          >
            <Input data-testid="user-id-input" />
          </Form.Item>
          <Form.Item
            name="name"
            label={intl.formatMessage({ id: 'users.modal.name', defaultMessage: 'Display name' })}
            rules={[{ required: true, message: intl.formatMessage({ id: 'users.modal.nameRequired', defaultMessage: 'Please enter the display name' }) }]}
          >
            <Input data-testid="user-name-input" />
          </Form.Item>
          <Form.Item name="role_ids" label={intl.formatMessage({ id: 'users.modal.roles', defaultMessage: 'Roles' })}>
            <Select
              mode="multiple"
              allowClear
              placeholder={intl.formatMessage({ id: 'users.modal.rolesPlaceholder', defaultMessage: 'Select one or more roles' })}
              options={roles.map((r) => ({ label: `${r.name} (${r.id})`, value: r.id }))}
              data-testid="user-role-select"
            />
          </Form.Item>
          <Form.Item
            name="scopes"
            label={intl.formatMessage({ id: 'users.modal.scopes', defaultMessage: 'Project scopes' })}
            tooltip={intl.formatMessage({ id: 'users.modal.scopesTip', defaultMessage: 'Comma separated project ids; * for all projects' })}
          >
            <Input placeholder="*" data-testid="user-scopes-input" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
