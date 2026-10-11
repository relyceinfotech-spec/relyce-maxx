-- Removed legacy schemas must no longer be exposed by the Data API.
alter role authenticator set pgrst.db_schemas = 'public,graphql_public';
notify pgrst, 'reload config';
notify pgrst, 'reload schema';
