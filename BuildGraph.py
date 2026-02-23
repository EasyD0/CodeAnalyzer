from neo4j import GraphDatabase
import json


class Neo4jGraphBuilder:
    """Neo4j图谱构建器
    用于将C代码分析器（CodeAnalyzer）的解析结果导入Neo4j图数据库，
    构建函数、全局变量及其调用/依赖关系的知识图谱
    """

    def __init__(self, uri: str, user: str, password: str):
        """
        初始化Neo4j连接驱动
        :param uri: Neo4j数据库连接地址（如"bolt://localhost:7687"）
        :param user: Neo4j数据库用户名
        :param password: Neo4j数据库密码
        """
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        """关闭Neo4j数据库连接驱动"""
        self.driver.close()

    def clear_database(self):
        """清空Neo4j数据库中所有节点和关系
        执行MATCH (n) DETACH DELETE n语句，删除所有节点及关联关系
        """
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def build_graph(self, analyzer):
        """核心方法：构建完整的代码知识图谱
        步骤：
        1. 创建函数节点
        2. 创建全局变量节点
        3. 创建函数调用关系（CALLS）
        4. 创建函数-全局变量使用关系（USES）
        5. 创建全局变量依赖关系（DEPENDS_ON）
        :param analyzer: 代码分析器实例，包含解析后的函数、变量、调用等信息
        """
        with self.driver.session() as session:
            # 1. 创建函数节点 - 复杂属性（如参数、数组访问）转为JSON字符串存储
            for (file_path, func_name), data in analyzer.functions.items():
                # 将复杂列表/字典类型属性序列化为JSON字符串
                params_json = json.dumps(data["params"])
                arrays_json = json.dumps(data["array_accesses"])
                var_chains_json = json.dumps(data["var_chains"])

                # 执行Cypher语句创建Function节点
                session.run(
                    """
                    CREATE (f:Function {
                        name: $name,
                        file: $file,
                        start_line: $start,
                        end_line: $end,
                        params_json: $params_json,
                        array_accesses_json: $arrays_json,
                        var_chains_json: $var_chains_json,
                        external_vars: $external_vars
                    })
                """,
                    name=func_name,
                    file=file_path,
                    start=data["start_line"],
                    end=data["end_line"],
                    params_json=params_json,
                    arrays_json=arrays_json,
                    var_chains_json=var_chains_json,
                    external_vars=data["external_vars"],
                )

            # 2. 创建全局变量节点
            for (file_path, var_name), data in analyzer.global_vars.items():
                session.run(
                    """
                    CREATE (v:GlobalVariable {
                        name: $name,
                        file: $file,
                        definition: $definition,
                        initializer: $initializer
                    })
                """,
                    name=var_name,
                    file=file_path,
                    definition=data["definition"],
                    initializer=data["initializer"],
                )

            # 3. 创建函数调用关系（CALLS）
            self._create_call_relations(session, analyzer)

            # 4. 创建函数-全局变量使用关系（USES）
            self._create_var_usage_relations(session, analyzer)

            # 5. 创建全局变量间依赖关系（DEPENDS_ON）
            self._create_var_dependency_relations(session, analyzer)

    def _create_call_relations(self, session, analyzer):
        """创建函数间调用关系（CALLS）
        仅当调用方和被调用方函数节点都存在，且参数链匹配时，创建关系
        :param session: Neo4j会话对象
        :param analyzer: 代码分析器实例
        """
        for call in analyzer.calls:
            # 获取调用方（caller）和被调用方（callee）的函数数据
            caller = analyzer.functions.get((call["caller_file"], call["caller_func"]))
            callee = None
            if call["callee_file"]:
                callee = analyzer.functions.get(
                    (call["callee_file"], call["callee_func"])
                )

            # 跳过调用方/被调用方不存在的情况
            if not caller or not callee:
                continue

            # 分析调用参数链是否匹配（判断是否创建关系）
            param_match = self._analyze_param_chain_match(
                caller, callee, call["arguments"], analyzer
            )

            if param_match:
                # 执行Cypher创建CALLS关系，包含参数匹配信息
                session.run(
                    """
                    MATCH (caller:Function {name: $caller_name, file: $caller_file})
                    MATCH (callee:Function {name: $callee_name, file: $callee_file})
                    CREATE (caller)-[:CALLS {direction: 'parent', matched_params: $matched_params}]->(callee)
                """,
                    caller_name=call["caller_func"],
                    caller_file=call["caller_file"],
                    callee_name=call["callee_func"],
                    callee_file=call["callee_file"],
                    matched_params=param_match,
                )

    def _analyze_param_chain_match(self, caller, callee, arguments, analyzer) -> bool:
        """分析函数调用的参数链是否匹配
        检查调用参数中的变量是否存在于调用方函数的变量链中，存在则返回True
        :param caller: 调用方函数数据
        :param callee: 被调用方函数数据（未使用，预留扩展）
        :param arguments: 调用参数列表
        :param analyzer: 代码分析器实例（未使用，预留扩展）
        :return: 参数链匹配返回True，否则返回False
        """
        # 解析调用方函数的变量链JSON字符串（异常时返回空字典）
        try:
            caller_var_chains = json.loads(caller.get("var_chains_json", "{}"))
        except:
            caller_var_chains = {}

        # 遍历调用参数，检查参数中的变量是否在调用方变量链中
        for arg in arguments:
            arg_vars = arg.get("vars", [])
            for var in arg_vars:
                if var in caller_var_chains:
                    return True
        return False

    def _create_var_usage_relations(self, session, analyzer):
        """创建函数-全局变量使用关系（USES）
        为每个函数使用的外部全局变量创建USES关系
        :param session: Neo4j会话对象
        :param analyzer: 代码分析器实例
        """
        for (file_path, func_name), func_data in analyzer.functions.items():
            for var_name in func_data["external_vars"]:
                # 执行Cypher匹配函数和全局变量节点，创建USES关系
                session.run(
                    """
                    MATCH (f:Function {name: $func_name, file: $func_file})
                    MATCH (v:GlobalVariable {name: $var_name})
                    CREATE (f)-[:USES]->(v)
                """,
                    func_name=func_name,
                    func_file=file_path,
                    var_name=var_name,
                )

    def _create_var_dependency_relations(self, session, analyzer):
        """创建全局变量间依赖关系（DEPENDS_ON）
        若变量初始化表达式依赖其他全局变量，则创建DEPENDS_ON关系
        :param session: Neo4j会话对象
        :param analyzer: 代码分析器实例
        """
        for (file_path, var_name), var_data in analyzer.global_vars.items():
            if var_data["initializer"]:
                # 从初始化表达式中提取依赖的变量列表
                deps = analyzer._extract_vars_from_expr(var_data["initializer"])
                for dep_var in deps:
                    # 执行Cypher匹配两个全局变量节点，创建DEPENDS_ON关系
                    session.run(
                        """
                        MATCH (v1:GlobalVariable {name: $var_name})
                        MATCH (v2:GlobalVariable {name: $dep_var})
                        CREATE (v1)-[:DEPENDS_ON]->(v2)
                    """,
                        var_name=var_name,
                        dep_var=dep_var,
                    )
