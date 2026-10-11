insert into public.topics(slug,name) values
 ('ai','Artificial Intelligence'),('programming','Programming'),('cybersecurity','Cybersecurity');
insert into public.topics(slug,name,parent_id)
select x.slug,x.name,t.id from (values
 ('ai/models','Models & LLMs','ai'),('ai/agents','AI Agents','ai'),
 ('ai/research','AI Research','ai'),('ai/tools','AI Tools','ai'),
 ('ai/robotics','Robotics & Vision','ai'),
 ('programming/web','Web Development','programming'),
 ('programming/languages','Python & Languages','programming'),
 ('programming/cloud','Databases & Cloud','programming'),
 ('programming/open-source','Open Source','programming'),
 ('programming/devops','DevOps & Tooling','programming'),
 ('cybersecurity/vulnerabilities','Vulnerabilities','cybersecurity'),
 ('cybersecurity/appsec','Application Security','cybersecurity'),
 ('cybersecurity/ai-security','AI Security','cybersecurity'),
 ('cybersecurity/privacy','Privacy','cybersecurity')
) x(slug,name,parent) join public.topics t on t.slug=x.parent;
