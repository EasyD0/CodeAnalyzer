import logging
import re
from pathlib import Path
from typing import Dict, List, Set

import clang.cindex
from clang.cindex import Cursor, CursorKind

import config


# %% C代码分析器核心类
class CCodeAnalyzer:
    """基于libclang解析C代码的静态分析器
    核心功能：
    1. 解析C文件的AST（抽象语法树）
    2. 提取函数、全局变量、局部变量、结构体等符号信息
    3. 分析函数调用关系、变量赋值链、数组访问等代码特征
    4. 构建函数调用链、变量变更链等分析结果
    """

    def __init__(self):
        # 初始化libclang库（需配置正确的clang库路径）
        clang.cindex.Config.set_library_path(
            config.CLANG_PATH
        )  # 从配置文件读取clang库路径

        # 创建clang索引对象，用于解析C文件
        self.index = clang.cindex.Index.create()
        self.include_paths = None  # 头文件包含路径（备用）

        # 存储分析结果的核心数据结构
        self.functions: Dict[str, Dict] = {}  # 函数字典：键为函数名，值为函数详细信息
        self.global_vars: Set[str] = set()  # 全局变量符号集合
        self.variables: Dict[str, Dict] = (
            {}
        )  # 变量字典：变量名 -> 变量类型/作用域/结构体信息等
        self.assignments: Dict[str, Dict] = {}  # 赋值关系：函数名 -> {变量名: 赋值信息}
        self.struct_definitions: Dict[str, str] = (
            {}
        )  # 结构体定义：结构体类型名 -> 结构体源码字符串

    def parse_file(
        self, file_path: str, include_paths: list[str] = None, defines: list[str] = None
    ):
        """解析单个C文件
        :param file_path: 待解析的C文件路径
        :param include_paths: 头文件包含路径列表
        :param defines: 宏定义列表（如["DEBUG=1", "PLATFORM=Linux"]）
        """
        logging.debug(f"开始解析文件: {file_path}")
        # 构建clang解析参数：指定语言为C，遵循C99标准
        args = ["-x", "c", "-std=c99"]

        # 添加头文件包含路径
        if include_paths:
            for p in include_paths:
                args.append(f"-I {p}")

        # 添加宏定义
        if defines:
            for d in defines:
                args.append(f"-D{d}")

        try:
            # 解析文件生成翻译单元（包含AST）
            translation_unit = self.index.parse(file_path, args=args)
            # 遍历AST进行代码分析
            self._traverse_ast(translation_unit.cursor, file_path)
            logging.info(f"成功解析文件: {file_path}")
        except Exception as e:
            logging.error(f"解析文件失败 {file_path}: {e}")

    def _traverse_ast(self, cursor: Cursor, file_path: str):
        """遍历AST（抽象语法树），提取全局变量和函数定义
        仅处理当前文件内的节点（排除头文件等外部文件节点）
        :param cursor: AST根游标节点
        :param file_path: 当前解析的文件路径
        """
        # 第一层遍历：收集全局变量和函数定义
        for child in cursor.get_children():
            # 过滤：仅处理当前文件的节点
            if (
                child.location.file
                and Path(child.location.file.name).resolve()
                == Path(file_path).resolve()
            ):
                # 全局变量声明节点
                if child.kind == CursorKind.VAR_DECL:
                    self._process_global_variable(child, file_path)
                # 函数声明/定义节点
                elif child.kind == CursorKind.FUNCTION_DECL:
                    self._process_function(child, file_path)

    def _process_global_variable(self, cursor: Cursor, file_path: str):
        """处理全局变量声明节点
        提取全局变量名称、类型、结构体字段等信息
        :param cursor: 全局变量游标节点
        :param file_path: 当前解析的文件路径
        """
        var_name = cursor.spelling  # 变量名
        if not var_name:
            return

        # 解析结构体字段（如果变量是结构体类型）
        struct_def = ""
        if cursor.type.spelling.startswith("struct"):
            struct_def = self._parse_struct_fields(cursor.type, file_path)

        # 记录全局变量
        self.global_vars.add(var_name)
        # 存储变量详细信息
        self.variables[var_name] = {
            "type": "global",  # 变量类型：全局
            "data_type": cursor.type.spelling,  # 数据类型（如int、struct XXX）
            "struct_fields": struct_def,  # 结构体字段源码（字符串形式）
            "assigned_from": None,  # 赋值来源（暂未使用）
        }
        logging.debug(f"发现全局变量: {var_name} ({cursor.type.spelling})")

    def _is_function_definition(self, func_cursor: Cursor) -> bool:
        """判断游标节点是否为函数定义（非声明）
        核心逻辑：函数定义包含函数体（COMPOUND_STMT节点），函数声明无函数体
        :param func_cursor: 函数游标节点
        :return: 是函数定义返回True，否则返回False
        """
        for child in func_cursor.get_children():
            if child.kind == CursorKind.COMPOUND_STMT:
                return True
        return False

    def _process_function(self, func_cursor: Cursor, file_path: str):
        """处理函数定义或声明节点
        函数定义：提取参数、函数体、变量使用等完整信息
        函数声明：仅初始化基础数据结构，不覆盖已有定义信息
        :param func_cursor: 函数游标节点
        :param file_path: 当前解析的文件路径
        """
        func_name = func_cursor.spelling  # 函数名
        if not func_name:
            return

        # 判断是函数定义还是声明
        is_definition = self._is_function_definition(func_cursor)

        if is_definition:
            logging.debug(f"处理函数定义: {func_name}")

            # 保留已有数据（如父函数调用关系），避免覆盖
            existing_data = self.functions.get(func_name, {})

            # 初始化/更新函数信息（核心字段）
            self.functions[func_name] = {
                "global": existing_data.get("global", []),  # 使用的全局变量列表
                "check": existing_data.get("check", False),  # 是否包含数组常量索引访问
                "change_chain": existing_data.get("change_chain", []),  # 变量变更链
                "call_chain": existing_data.get("call_chain", []),  # 函数调用链
                "parent": existing_data.get("parent", False),  # 是否依赖父函数参数
                "parent_call": existing_data.get(
                    "parent_call", []
                ),  # 调用当前函数的父函数列表（保留已有）
                "params": existing_data.get("params", {}),  # 函数参数信息
                "local_vars": existing_data.get("local_vars", set()),  # 局部变量集合
                "assignments": existing_data.get("assignments", {}),  # 赋值关系
            }

            # 初始化赋值记录
            if func_name not in self.assignments:
                self.assignments[func_name] = {}

            # 处理函数参数（定义中的参数信息更完整）
            for param in func_cursor.get_arguments():
                self._process_parameter(param, func_name, file_path)

            # 处理函数体（核心逻辑）
            self._process_function_body(func_cursor, func_name, file_path)

        else:
            logging.debug(f"处理函数声明: {func_name}")
            # 仅初始化空数据，不覆盖已有定义信息
            if func_name not in self.functions:
                self.functions[func_name] = {
                    "global": [],
                    "check": False,
                    "change_chain": [],
                    "call_chain": [],
                    "parent": False,
                    "parent_call": [],  # 父函数调用列表
                    "params": {},
                    "local_vars": set(),
                    "assignments": {},
                }
                self.assignments[func_name] = {}

    def _process_parameter(self, cursor: Cursor, func_name: str, file_path: str):
        """处理函数参数节点
        提取参数名称、类型、结构体字段等信息
        :param cursor: 参数游标节点
        :param func_name: 所属函数名
        :param file_path: 当前解析的文件路径
        """
        param_name = cursor.spelling  # 参数名
        if not param_name:
            return

        # 解析结构体字段（如果参数是结构体类型）
        struct_def = ""
        if cursor.type.spelling.startswith("struct"):
            struct_def = self._parse_struct_fields(cursor.type, file_path)

        # 存储参数信息
        self.functions[func_name]["params"][param_name] = {
            "type": "param",  # 变量类型：参数
            "data_type": cursor.type.spelling,  # 数据类型
            "struct_fields": struct_def,  # 结构体字段源码
            "func_scope": func_name,  # 所属函数作用域
        }
        logging.debug(f"  函数参数: {param_name}")

    def _process_function_body(self, cursor: Cursor, func_name: str, file_path: str):
        """递归处理函数体节点
        提取局部变量、变量引用、数组访问、赋值操作、函数调用等信息
        :param cursor: 函数体游标节点
        :param func_name: 所属函数名
        :param file_path: 当前解析的文件路径
        """
        for child in cursor.get_children():
            # 过滤：仅处理当前文件的节点
            if child.location.file and child.location.file.name == file_path:
                # 局部变量声明节点
                if child.kind == CursorKind.VAR_DECL:
                    self._process_local_variable(child, func_name, file_path)

                # 变量引用节点（如使用全局变量）
                elif child.kind == CursorKind.DECL_REF_EXPR:
                    self._process_variable_reference(child, func_name)

                # 数组下标访问节点（如arr[i]）
                elif child.kind == CursorKind.ARRAY_SUBSCRIPT_EXPR:
                    self._process_array_subscript(child, func_name, file_path)

                # 赋值运算符节点（=）
                elif child.kind == CursorKind.BINARY_OPERATOR and child.spelling == "=":
                    self._process_assignment(child, func_name, file_path)

                # 函数调用节点（如func()）
                elif child.kind == CursorKind.CALL_EXPR:
                    self._process_function_call(child, func_name, file_path)

                # 递归处理子节点（覆盖嵌套代码块）
                self._process_function_body(child, func_name, file_path)

    def _process_local_variable(self, cursor: Cursor, func_name: str, file_path: str):
        """处理局部变量声明节点
        提取局部变量名称、类型、结构体字段等信息
        :param cursor: 局部变量游标节点
        :param func_name: 所属函数名
        :param file_path: 当前解析的文件路径
        """
        var_name = cursor.spelling  # 变量名
        if not var_name:
            return

        # 记录局部变量
        self.functions[func_name]["local_vars"].add(var_name)
        # 存储变量详细信息
        self.variables[var_name] = {
            "type": "local",  # 变量类型：局部
            "data_type": cursor.type.spelling,  # 数据类型
            "struct_fields": self._parse_struct_fields(
                cursor.type, file_path
            ),  # 结构体字段
            "func_scope": func_name,  # 所属函数作用域
        }
        logging.debug(f"    局部变量: {var_name}")

    def _process_variable_reference(self, cursor: Cursor, func_name: str):
        """处理变量引用节点
        识别函数中使用的全局变量并记录
        :param cursor: 变量引用游标节点
        :param func_name: 所属函数名
        """
        var_name = cursor.spelling  # 变量名
        # 如果是全局变量且未记录，则添加到函数的全局变量列表
        if (
            var_name in self.global_vars
            and var_name not in self.functions[func_name]["global"]
        ):
            self.functions[func_name]["global"].append(var_name)

    def _process_array_subscript(self, cursor: Cursor, func_name: str, file_path: str):
        """处理数组下标访问节点
        分析数组索引类型（常量/函数调用/变量），构建变量变更链
        :param cursor: 数组下标游标节点
        :param func_name: 所属函数名
        :param file_path: 当前解析的文件路径
        """
        children = list(cursor.get_children())
        if len(children) < 2:
            return

        index_expr = children[1]  # 数组索引表达式节点（如arr[5]中的5）

        # 场景1：常量索引（如arr[5]）- 标记check为True
        if index_expr.kind == CursorKind.INTEGER_LITERAL:
            self.functions[func_name]["check"] = True
            logging.debug(f"    数组常量索引: {index_expr.spelling}")
            return

        # 场景2：函数调用作为索引（如arr[get_idx()]）
        if index_expr.kind == CursorKind.CALL_EXPR:
            callee_name = index_expr.spelling
            if callee_name:
                self.functions[func_name]["change_chain"].append(callee_name)
                self.functions[func_name]["call_chain"].append(callee_name)
                logging.debug(f"    数组函数索引: {callee_name}")
            return

        # 场景3：变量作为索引（如arr[idx]）
        if index_expr.kind == CursorKind.DECL_REF_EXPR:
            var_name = index_expr.spelling
            # 构建变量变更链（追踪索引变量的赋值来源）
            change_chain = self._build_change_chain(var_name, func_name, file_path)

            if change_chain:
                # 场景3.1：变更链最后一个元素是函数参数 - 标记parent为True
                if change_chain[-1] in self.functions[func_name]["params"]:
                    self.functions[func_name]["parent"] = True
                    logging.debug(f"    数组参数索引: {change_chain[-1]}")

                # 场景3.2：变更链包含其他函数 - 添加到调用链/变更链
                for item in change_chain:
                    if item in self.functions and item != func_name:
                        self.functions[func_name]["change_chain"].append(item)
                        self.functions[func_name]["call_chain"].append(item)
                        logging.debug(f"    数组变量索引关联函数: {item}")

                # 场景3.3：变更链最后一个元素是全局变量 - 追踪赋值来源
                if change_chain[-1] in self.global_vars:
                    assigned_from = self.variables[change_chain[-1]].get(
                        "assigned_from"
                    )
                    if assigned_from:
                        self.functions[func_name]["change_chain"].extend(
                            [assigned_from, change_chain[-1]]
                        )
                        self.functions[func_name]["call_chain"].append(assigned_from)
                        logging.debug(f"    数组全局变量索引赋值来源: {assigned_from}")

    def _build_change_chain(
        self, var_name: str, func_name: str, file_path: str, visited: Set[str] = None
    ) -> List[str]:
        """递归构建变量变更链
        追踪变量的赋值来源，形成变量->变量/函数的变更链条（防止循环引用）
        :param var_name: 起始变量名
        :param func_name: 所属函数名
        :param file_path: 当前解析的文件路径
        :param visited: 已访问变量集合（防止循环引用）
        :return: 变量变更链列表
        """
        if visited is None:
            visited = set()

        chain = [var_name]  # 初始化变更链

        # 防止循环引用（如a = b; b = a;）
        if var_name in visited:
            return chain
        visited.add(var_name)

        # 查找变量的赋值信息
        if func_name in self.assignments and var_name in self.assignments[func_name]:
            assign_info = self.assignments[func_name][var_name]

            # 场景1：变量赋值自函数调用（如idx = get_idx()）
            if assign_info["type"] == "function_call":
                chain.append(assign_info["value"])
            # 场景2：变量赋值自其他变量（如idx = a / 2）
            elif assign_info["type"] == "variable":
                source_var = assign_info["value"]
                # 递归构建来源变量的变更链
                sub_chain = self._build_change_chain(
                    source_var, func_name, file_path, visited
                )
                chain.extend(sub_chain[1:])  # 合并子链（排除重复的起始变量）

        return chain

    def _process_assignment(self, cursor: Cursor, func_name: str, file_path: str):
        """处理赋值操作节点
        记录变量的赋值来源（函数调用/其他变量/表达式）
        :param cursor: 赋值运算符游标节点
        :param func_name: 所属函数名
        :param file_path: 当前解析的文件路径
        """
        children = list(cursor.get_children())
        if len(children) < 2:
            return

        lhs = children[0]  # 左值（被赋值变量）
        rhs = children[1]  # 右值（赋值来源）

        # 仅处理左值为变量引用的情况
        if lhs.kind != CursorKind.DECL_REF_EXPR:
            return

        var_name = lhs.spelling  # 被赋值变量名

        # 场景1：右值是函数调用（如idx = get_idx()）
        if rhs.kind == CursorKind.CALL_EXPR:
            callee_name = rhs.spelling
            if callee_name:
                self.assignments[func_name][var_name] = {
                    "type": "function_call",
                    "value": callee_name,
                }
                logging.debug(f"    赋值操作: {var_name} = {callee_name}()")
        # 场景2：右值是变量引用（如idx = a）
        elif rhs.kind == CursorKind.DECL_REF_EXPR:
            source_var = rhs.spelling
            self.assignments[func_name][var_name] = {
                "type": "variable",
                "value": source_var,
            }
            logging.debug(f"    赋值操作: {var_name} = {source_var}")
        # 场景3：右值是二元表达式（如idx = a / 2）
        elif rhs.kind == CursorKind.BINARY_OPERATOR:
            # 提取表达式中的变量
            vars_in_expr = self._extract_variables(rhs)
            if vars_in_expr:
                # 记录第一个变量作为赋值来源
                self.assignments[func_name][var_name] = {
                    "type": "variable",
                    "value": vars_in_expr[0],
                }
                logging.debug(
                    f"    赋值操作: {var_name} = <> (表达式变量: {vars_in_expr})"
                )

    def _extract_variables(self, cursor: Cursor) -> List[str]:
        """递归提取表达式中的所有变量名
        :param cursor: 表达式游标节点
        :return: 变量名列表
        """
        vars_list = []
        # 变量引用节点：直接记录变量名
        if cursor.kind == CursorKind.DECL_REF_EXPR:
            vars_list.append(cursor.spelling)
        # 其他节点：递归遍历子节点
        else:
            for child in cursor.get_children():
                vars_list.extend(self._extract_variables(child))
        return vars_list

    def _process_function_call(self, cursor: Cursor, func_name: str, file_path: str):
        """处理函数调用节点
        记录函数调用关系（调用方->被调用方），更新被调用方的父函数列表
        :param cursor: 函数调用游标节点
        :param func_name: 调用方函数名
        :param file_path: 当前解析的文件路径
        """
        callee_name = cursor.spelling  # 被调用函数名
        # 过滤：空函数名或自调用
        if not callee_name or callee_name == func_name:
            return

        # 初始化被调用函数的信息（如果不存在）
        if callee_name not in self.functions:
            self.functions[callee_name] = {
                "global": [],
                "check": False,
                "change_chain": [],
                "call_chain": [],
                "parent": False,
                "parent_call": [func_name],  # 记录调用方
                "params": {},
                "local_vars": set(),
                "assignments": {},
            }
        else:
            # 避免重复记录调用关系
            if func_name not in self.functions[callee_name]["parent_call"]:
                self.functions[callee_name]["parent_call"].append(func_name)

        logging.debug(f"    函数调用: {func_name} -> {callee_name}")

    def _parse_struct_fields(self, type_obj, file_path: str) -> str:
        """解析结构体类型的字段定义
        从源码中提取结构体的完整定义（包含嵌套结构体），格式化后存储
        :param type_obj: 结构体类型对象
        :param file_path: 当前解析的文件路径
        :return: 格式化后的结构体源码字符串
        """
        type_spelling = type_obj.spelling  # 结构体类型名（如struct XXX）

        # 非结构体类型直接返回空字符串
        if not type_spelling.startswith("struct"):
            return ""

        # 已解析过的结构体直接返回缓存结果
        if type_spelling in self.struct_definitions:
            return self.struct_definitions[type_spelling]

        # 获取结构体声明游标
        type_def = type_obj.get_declaration()
        if not type_def or type_def.kind != CursorKind.STRUCT_DECL:
            # 无法解析结构体定义，返回类型名
            return type_spelling

        # 从源码中提取结构体定义
        try:
            extent = type_def.extent  # 结构体在源码中的位置（起始/结束行）

            # 过滤：仅处理当前文件的结构体定义
            if not extent.start.file or extent.start.file.name != file_path:
                return type_spelling

            start_line = extent.start.line
            end_line = extent.end.line

            # 读取结构体源码行
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                # clang的行号是1-based，列表是0-based，需转换
                struct_lines = lines[start_line - 1 : end_line]
                struct_code = "".join(struct_lines).strip()

                # 格式化源码：统一空格，换行分隔字段
                struct_code = re.sub(r"\s+", " ", struct_code)
                struct_code = struct_code.replace("{ ", "{\n").replace("; ", ";\n")

                # 缓存结构体定义
                self.struct_definitions[type_spelling] = struct_code

                # 解析嵌套结构体
                self._extract_nested_structs(type_def, file_path)

                return struct_code

        except Exception as e:
            logging.debug(f"解析结构体失败: {type_spelling}: {e}")
            return type_spelling

    def _extract_nested_structs(self, struct_cursor, file_path: str):
        """递归提取结构体中的嵌套结构体定义
        确保所有嵌套结构体都被解析并缓存
        :param struct_cursor: 结构体游标节点
        :param file_path: 当前解析的文件路径
        """
        try:
            # 遍历结构体字段
            for field in struct_cursor.get_children():
                if field.kind == CursorKind.FIELD_DECL:
                    field_type = field.type
                    field_type_name = field_type.spelling

                    # 嵌套结构体且未解析过
                    if (
                        field_type_name.startswith("struct")
                        and field_type_name not in self.struct_definitions
                    ):
                        # 递归解析嵌套结构体
                        self._parse_struct_fields(field_type, file_path)
        except Exception as e:
            logging.debug(f"解析嵌套结构体失败: {e}")

    def get_analysis_result(self) -> Dict:
        """获取最终的分析结果
        :return: 包含函数、全局变量、变量信息的字典
        """
        return {
            "functions": self.functions,
            "global_vars": list(self.global_vars),
            "variables": self.variables,
        }


# %% 测试函数
def test1():
    """测试函数：初始化项目路径（示例）"""
    proj_path = Path(r"E:\Documents\Code\Python\EngineIndex\ForTest")
    pass


if __name__ == "__main__":
    test1()
