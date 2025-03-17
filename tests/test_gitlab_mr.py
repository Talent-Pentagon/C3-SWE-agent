import os
from unittest import mock

import pytest

from sweagent.agent.problem_statement import GitlabIssue
from sweagent.run.hooks.open_pr import (
    OpenPRConfig,
    OpenPRHook,
    _determine_repo_type,
)
from sweagent.types import AgentRunResult


class TestDetermineRepoType:
    """Test _determine_repo_type function"""

    def test_github_url(self):
        """Test with GitHub URL"""
        repo_type = _determine_repo_type("https://github.com/user/repo/issues/1")
        assert repo_type == "github"

    def test_gitlab_url(self):
        """Test with GitLab URL"""
        repo_type = _determine_repo_type("https://gitlab.com/user/repo/-/issues/1")
        assert repo_type == "gitlab"

    def test_custom_gitlab_url(self):
        """Test with custom GitLab instance URL"""
        repo_type = _determine_repo_type("https://gitlab.example.com/user/repo/-/issues/1")
        assert repo_type == "gitlab"

    def test_unknown_url(self):
        """Test with unknown URL"""
        repo_type = _determine_repo_type("https://example.com/user/repo")
        assert repo_type == "github"  # Default to GitHub for backward compatibility


@mock.patch.dict(os.environ, {"GITLAB_TOKEN": "test_token", "GITLAB_TOKEN_TYPE": "project"})
class TestOpenPRHookWithGitlab:
    """Test OpenPRHook with GitLab issues"""

    @pytest.fixture
    def open_pr_hook_init_for_gitlab(self):
        """Fixture for OpenPRHook initialized with GitLab issue"""
        hook = OpenPRHook(config=OpenPRConfig(skip_if_commits_reference_issue=True))
        hook._github_token = os.environ.get("GITHUB_TOKEN", "")
        hook._gitlab_token = os.environ.get("GITLAB_TOKEN", "")
        hook._gitlab_token_type = os.environ.get("GITLAB_TOKEN_TYPE", "project")
        hook._problem_statement = GitlabIssue(gitlab_url="https://gitlab.com/jpaodev/test-repo/-/issues/1")
        # Set the issue_url property for the hook
        hook._issue_url = "https://gitlab.com/jpaodev/test-repo/-/issues/1"
        return hook

    @pytest.fixture
    def agent_run_result(self):
        """Fixture for AgentRunResult"""
        return AgentRunResult(
            info={
                "submission": "test_submission",
                "exit_status": "submitted",
            },
            trajectory=[],
        )

    def test_should_open_pr_gitlab_open_issue(self, open_pr_hook_init_for_gitlab, agent_run_result):
        """Test should_open_pr with open GitLab issue"""
        hook = open_pr_hook_init_for_gitlab

        # Create a separate test for open issues
        with mock.patch("sweagent.utils.gitlab._is_gitlab_issue_url", return_value=True):
            with mock.patch(
                "sweagent.utils.gitlab._parse_gitlab_issue_url",
                return_value=("https://gitlab.com", "jpaodev", "test-repo", "1"),
            ):
                with mock.patch(
                    "sweagent.utils.gitlab._get_gitlab_issue_data",
                    return_value={
                        "iid": 1,
                        "title": "Test Issue",
                        "description": "Test Description",
                        "state": "opened",  # Open state
                        "assignee": None,
                        "discussion_locked": False,
                    },
                ):
                    with mock.patch("sweagent.run.hooks.open_pr._get_gitlab_associated_commit_urls", return_value=[]):
                        # Test should_open_pr - should succeed with open issue
                        result = hook.should_open_pr(agent_run_result)
                        assert result, "should_open_pr should return True for open issue"
