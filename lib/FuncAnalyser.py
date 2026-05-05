"""
注意事项, 必须使用深度优先搜索, 当子作用域定义重名符号时, 进行标识符隐藏
当退出作用域时, 消除那个作用域中局部变量的顶点
那么实际上, 局部变量应该作为一个栈存放, 当需要使用局部变量时, 从栈顶开始向下查找, 有什么办法可以减少
"""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
import clang.cindex
from clang.cindex import Cursor, CursorKind, StorageClass, TypeKind, Type
from .LogSet import logSetup

from .Common import (
    CodeLocation,
    TypeCategory,
    VarNode,
    Parameter,
    ReturnValNode,
    FuncBase,
    FuncPointee,
    FuncNode,
    GlobalVarNode,
    LocalVarNode,
    GlobalVarDict,
    FuncDict,
    memberListParseRecursive,
)

from .AnalyzerHelper import (
    PreorderAST,  # 用于遍历AST, 且可控制是否继续
    is_arrIdxNode,
    is_callFuncNode,
    is_callFuncParam,
    is_callGlobalVarNode,
    is_callLocalVarNode,
    is_callVarNode,
    is_globalVarDeclNode,
    is_ifStmt,
    is_localVarDeclNode,  #
    is_switchStmt,
    is_varAssignNode,  # x = y
    is_forLoopNode,  # for(i = 0; i<x; ++i)
    is_whileLoopNode,  # while(n < 100)
    is_returnStatement,
    is_parameterRefNode,
    #
    is_funcDecl,
    is_funcDef,
    #
    is_static,
    is_array,
    is_pointer,
    is_struct,
    is_union,
    is_func_pointer,
    #
    typeName,
    pureTypeName,
)

logger = logSetup()


def parameterContraction(func: FuncNode):
    """
    收缩形参所关联的节点, 收缩局部变量关联的节点
    注意: 形参可能和其他函数形参关联, 这如何收缩?
    """
    pass


def func_return_params_parse_recursive(func_node: FuncBase, cursor: Cursor):
    """
    为函数节点的返回值注册信息
    """
    pass


def eliminateVertex(edge: set[tuple[Any, Any]], vertex: Iterable[Any]):
    """
    从有向图中删除指定的节点，并通过添加前驱->后继的边来保持连通性。
    """
    # 拷贝一份
    remaining_edges = set(edge)

    # 临时存储被删除节点的入边和出边
    incoming = {}  # node -> set of predecessors
    outgoing = {}  # node -> set of successors

    for node in vertex:
        incoming[node] = set()
        outgoing[node] = set()

    # 遍历所有边，收集被删除节点的入边和出边
    for src, tgt in edge:
        if tgt in vertex:
            incoming[tgt].add(src)
        if src in vertex:
            outgoing[src].add(tgt)

    # 添加绕过边：对于每个被删除的节点 v，添加所有 u -> w，其中 u -> v 且 v -> w
    for node in vertex:
        for predecessor in incoming[node]:
            for successor in outgoing[node]:
                # 如果前驱或后继也被删除，则不应连接
                if predecessor not in vertex and successor not in vertex:
                    remaining_edges.add((predecessor, successor))

    # 移除所有与被删除节点相关的边
    edges_to_remove = set()
    for edge in remaining_edges:
        src, tgt = edge
        if src in vertex or tgt in vertex:
            edges_to_remove.add(edge)

    remaining_edges -= edges_to_remove

    return remaining_edges


class FuncAnalyzer:
    """
    函数声明和函数体分析器
    """

    all_func_dict = FuncDict
    all_gvar_dict = GlobalVarDict

    def simplifySubgraph(self):
        """
        简化子图, 删除局部变量节点
        """
        raise NotImplementedError

    def update(self):
        """
        将子图信息, 函数信息添加到全局数据中
        """
        raise NotImplementedError

    def __init__(self, func_node: Cursor):
        self.func_node = func_node
        self.subgraph: set[tuple] = set()
        self.is_def_node = is_funcDef(func_node)
        self.func_name = func_node.spelling
        self.result: FuncNode = None

    def do(self):
        if self.is_def_node:
            self.process_funcDeclNode()
        else:
            self.process_funcDefNode()
            self.parseSubgraph()
            self.update()

    def _getFuncParams(self) -> list[Parameter]:
        """
        构建函数形参列表信息, 返回一个列表
        """

        node = self.func_node
        parameters: list[Parameter] = []
        for i, p in enumerate(node.get_arguments()):
            p_type = p.type
            p_type_name = p_type.spelling
            # 处理形参列表为(void)的情况
            if p_type_name == "void":
                break

            # 处理可能的匿名参数 (如 void func(int);)
            p_name = p.spelling if p.spelling else f"parameter{i}"
            parameter = Parameter(
                name=p_name,
                position=i,
                type_name=p_type_name,
                pure_type_name=pureTypeName(p),
                var_category=TypeCategory.from_cursor(node),
                is_member=False,
            )

            memberListParseRecursive(parameter, p_type)
            parameters.append(parameter)
        return parameters

    def _getReturnVal(self) -> ReturnValNode:
        """
        构建返回值节点
        """
        return ReturnValNode.from_cursor(self.func_node)

    def process_funcDeclNode(self) -> FuncNode | None:
        """
        处理函数声明节点, 获取基本信息
        """
        node = self.func_node
        func_name = node.spelling
        if not func_name:
            return None

        # 已经解析过, 则直接返回
        if func_name in FuncDict:
            self.result = FuncDict[func_name]
            return FuncDict[func_name]

        return_type_name = node.result_type.get_canonical().spelling

        self.result = FuncNode(
            name=func_name,
            parameters=self._getFuncParams(),
            return_type=return_type_name,
            return_var=self._getReturnVal(),
            def_location=CodeLocation.from_cursor(node),
        )

        FuncDict[func_name] = self.result
        return self.result

    def process_funcDefNode(
        self,
    ):
        """
        处理函数定义节点，深度遍历函数内所有子节点
        :param root_node: 函数定义对应的游标节点
        :param subgraph: 子图，用于记录函数内的节点关系
        """
        func_name = self.func_name
        # 局部变量字典, 记录局部变量的基本信息, 用于构建subgraph
        self.LocalVarDict: defaultdict[str, LocalVarNode] = defaultdict()
        # 一个函数内 所引用东西关联图, 每个元素是一个有向边, 一个有向边为tuple[src, dst] TODO 这就必须要求每个节点是不可变的或者可哈希的? 这有些麻烦了
        self.subgraph: set[tuple[Any, Any]] = set()

        if not func_name:
            logger.error("函数名不存在")
            return

        if func_name not in FuncDict:
            self.process_funcDeclNode()

        if FuncDict[func_name].def_location is not None:
            logger.warning(
                f"函数{func_name}在 {FuncDict[func_name].def_location} 已有定义"
            )
            return

        # 更新形参名称
        for i, p in enumerate(self.func_node.get_arguments()):
            p_type_name = typeName(p.type.get_canonical())
            if p_type_name == "void":  # 处理形参列表为(void)的情况
                break

            p_name = p.spelling if p.spelling else f"parameter{i}"

            # TODO 需要防止代码中出现声明和定义不一致的情况
            FuncDict[func_name].parameters[i].name = p_name

        # 更新def_location
        FuncDict[func_name].def_location = CodeLocation.from_cursor(self.func_node)

        # pre_node = None
        # for cur_node in node.walk_preorder():
        #     self.process_subNodeInFunc(
        #         func_name,
        #         sub_graph,
        #         localvar_dict,
        #         cur_node,
        #         pre_node,
        #     )
        #     pre_node = cur_node

        PreorderAST(
            self.func_node,
            lambda node: self.process_subNode(node),
            lambda node: node.get_children(),
        )

    def process_subNode(
        self,
        cur_node: Cursor,
        # pre_node: Cursor = None,
    ) -> bool:
        """
        处理函数内的子节点，分析函数调用、数组索引、枚举调用等
        函数内部节点的核心处理入口
        :param cur_node: 当前遍历的游标节点
        :param pre_node: 上一个遍历的游标节点（默认None）
        :return: 返回值表示是否需要递归当前节点的子树?
        """
        if is_callLocalVarNode(cur_node):
            self.process_callLocalVarNode(cur_node)
            return True

        elif is_callGlobalVarNode(cur_node):
            self.process_callGlobalVarNode(cur_node)
            return True

        elif is_localVarDeclNode(cur_node):
            self.process_localVarDeclNode(cur_node)
            return True

        elif is_varAssignNode(cur_node):
            self.process_varAssignNode(cur_node)
            return False

        elif is_callFuncNode(cur_node):
            self.process_callFuncNode(cur_node)
            return False

        elif is_arrIdxNode(cur_node):
            # self.process_arrIdxNode(cur_node, pre_node, subgraph)
            self.process_arrIdxNode(cur_node)
            return False

        elif is_forLoopNode(cur_node):
            self.process_forLoopNode(cur_node)
            return False

        elif is_whileLoopNode(cur_node):
            self.process_whileLoopNode(cur_node)
            return False

        elif is_parameterRefNode(cur_node):
            self.process_parameterRefNode(cur_node)
            return False

        elif is_returnStatement(cur_node):
            self.process_returnStatement(cur_node)
            return False

        return True
        # self.process_callEnumNode(cur_node, subgraph)

    def process_callLocalVarNode(self, node: Cursor):
        """
        处理局部变量调用节点
        提取函数内调用的局部变量信息（未实现具体逻辑）
        :param node: 局部变量调用对应的游标节点
        """
        if not is_callLocalVarNode(node):
            return

        # TODO 仅仅调用局部变量不能说明什么?
        pass

    def process_callGlobalVarNode(self, node: Cursor):
        """
        处理全局变量调用节点
        提取函数内调用的全局变量信息（未实现具体逻辑）
        :param node: 全局变量调用对应的游标节点
        """
        # 待实现：解析函数内调用的全局变量，更新FuncNode的called_var
        if not is_callGlobalVarNode(node):
            return

        # TODO 这里需要判断它是否是一个结构体成员, 数组类型等等?
        global_var_name = node.spelling
        func_node = FuncDict.get(self.func_name)

        if not global_var_name in GlobalVarDict:
            # 如果未出现过, 需要解析下这个全局变量节点
            var_decl = node.referenced
            self.process_globalVarNode(var_decl)

        if not global_var_name in GlobalVarDict:
            logger.error(f"全局变量 {global_var_name} 无法解析")
            return

        global_var_node = GlobalVarDict[global_var_name]
        if not (func_node, global_var_node) in self.subgraph:
            self.subgraph.add((func_node, global_var_node))

        func_node.called_var.add(global_var_name)

        raise NotImplementedError

    def process_localVarDeclNode(self, node: Cursor):
        """
        处理局部变量声明节点
        提取函数内定义的局部变量信息（未实现具体逻辑）
        :param node: 局部变量声明对应的游标节点
        """
        # 记录局部变量的声明
        # 生成一个 localVar 对象, 加入到 localvar_dict 中

        if not is_localVarDeclNode(node):
            return

        var_name = node.spelling
        if not var_name:
            return

        if var_name in self.LocalVarDict:
            # TODO 这里要考虑如果在不同的子块作用域, 可能有同名的不同实体的局部变量
            return

        _type_name = typeName(node)
        _pure_type_name = pureTypeName(node)
        _is_static = is_static(node)

        local_var = LocalVarNode(
            name=var_name,
            type_name=_type_name,
            pure_type_name=_pure_type_name,
            var_category=TypeCategory.from_cursor(node),
            is_static=_is_static,
            in_func=self.func_name,
        )

        self.LocalVarDict[var_name] = local_var
        memberListParseRecursive(local_var, node.type)

        # 增加图连接关系
        self.subgraph.add((FuncDict[self.func_name], local_var))

    def process_varAssignNode(self, node: Cursor):
        """
        处理变量赋值节点
        解析代码中的变量赋值操作，提取赋值关系（如变量被赋予的值、赋值位置等）
        :param node: 赋值操作对应的clang游标节点
        """
        # 待实现：解析赋值表达式，记录变量赋值的目标变量、赋值来源（常量/变量/表达式）等信息
        # 如果左右两边都是含有变量的表达式, 则将他们关联起来
        if not is_varAssignNode(node):
            return
        children = list(node.get_children())
        left_expr: Cursor = children[0]
        right_expr: Cursor = children[1]

        var_in_left_expr: list

        # 最简单的情况 x = y
        # 访问数组的情况 x[1] = y
        # 复杂的情况 *x[1] = y, x.member = y
        pass

    def process_callFuncNode(self, node: Cursor):
        """
        处理函数调用节点
        识别代码中调用的函数，记录调用关系（如当前函数调用了哪些其他函数）
        :param node: 函数调用对应的clang游标节点
        """
        # 待实现：提取被调用函数的名称，更新对应FuncNode的called_func列表
        # 检查传入的实参, 如果是全局变量, 则应该有关联关系
        if not is_callFuncNode(node):
            return

        called_func_name = None
        called_func_decl_node: Cursor | None = None
        for child in node.get_children():
            if child.kind == CursorKind.DECL_REF_EXPR and child.referenced:
                called_func_decl_node = child.referenced
                called_func_name = called_func_decl_node.spelling
                break
        if not called_func_name:
            return

        cur_func_node = FuncDict[self.func_name]
        if called_func_name not in FuncDict:
            self.process_funcDeclNode(called_func_decl_node)
        called_func_node = FuncDict.get(called_func_name)

        args = list(node.get_arguments())
        for idx, expr in enumerate(args):

            # 一个实参中所含有的变量节点
            list_nodes: list[VarNode] = self.prase_expression(expr)
            for node in list_nodes:
                # 添加子图关系
                self.subgraph.add((called_func_node.parameters[idx], node))

            # arg_vars = self._extract_variable_names_from_expr(arg)
            # for var_name in arg_vars:
            #     if var_name in GlobalVarDict:
            #         global_var_node = GlobalVarDict[var_name]
            #         if (cur_func_node, global_var_node) not in subgraph:
            #             subgraph.add((cur_func_node, global_var_node))
            #         cur_func_node.called_var.add(var_name)
        # TODO

    def process_callEnumNode(self, node: Cursor):
        """
        处理枚举值调用节点
        识别代码中使用的枚举常量，提取枚举相关信息
        :param node: 枚举值使用对应的clang游标节点
        """
        # 1. 解析枚举节点，识别当前使用的枚举常量名称和所属枚举类型
        # 1.1 获取枚举常量的拼写名称（如枚举值标识符）
        # 1.2 记录枚举常量的定义位置和调用位置，补充至CodeLocation对象
        pass

    def process_parameterRefNode(self, node: Cursor):
        """
        处理函数体内, 形参使用的节点
        """
        pass

    def process_arrIdxNode(
        self,
        cur_node: Cursor,
        # pre_node: Cursor,
    ):
        """
        处理数组索引访问节点
        解析数组下标访问操作，分析索引的构成（常量/表达式）
        :param cur_node: 当前遍历到的游标节点（通常是数组索引节点）
        :param pre_node: 上一个遍历的游标节点（通常是数组变量节点）
        """
        # 检查数组下标是字面量, 还是表达式, 并解析表达式中含有的变量, 这些变量将和数组关联

        if not is_arrIdxNode(cur_node):
            return

        pass

    def process_forLoopNode(self, node: Cursor):
        """
        处理for循环节点, 边界语句所含有的变量关联信息
        TODO 只处理规范形式的for语句, 否则将跳过?
        """

        # 定位到for 语句的第二个表达式 即 for(int i = 0; i < x; i++) 中的 i < x
        # 检查second_expr 是否为空, 然后检查它是否是一个布尔表达式, 含有 <  <=  ==  > >= 等情况
        # 检查 布尔表达式 两边的 表达式, 如果两边都含有变量, 则将他们关联起来, 复杂的情况是 i + j < x + y
        # 那么将 i 分别和 x, y, j 关联, 关联关系放到子图subgraph中

        if node.kind != CursorKind.FOR_STMT:
            return
        children = list(node.get_children())
        if len(children) < 3:
            return

        init_expr = children[0]
        condition_expr = children[1]

        raise NotImplementedError

    def process_whileLoopNode(self, node: Cursor):
        """类似上面的情况"""
        if node.kind != CursorKind.WHILE_STMT:
            return
        children = list(node.get_children())
        if len(children) == 0:
            return

        expr = children[0]
        left_nodes, right_nodes = self.prase_bool_expression(expr)
        if not right_nodes and len(left_nodes) == 1:
            return
        else:

            pass

    def _extract_variable_names_from_expr(self, node: Cursor) -> set[str]:
        """
        递归提取表达式中涉及的所有变量名（全局或局部）
        """
        vars_found = set()

        def _traverse(n: Cursor):
            if n.kind.is_expression() and n.spelling and n.spelling in GlobalVarDict:
                vars_found.add(n.spelling)
            elif n.kind.is_declaration() and n.spelling:
                # 局部变量也可能被使用
                pass  # 局部变量暂不参与全局关联
            for child in n.get_children():
                _traverse(child)

        _traverse(node)
        return vars_found

    def process_loop_condition(self, condition_expr: Cursor, subgraph):
        """
        处理循环条件中的变量关联
        """
        if not condition_expr:
            return

        if condition_expr.kind != CursorKind.BINARY_OPERATOR:
            return

        op = condition_expr.displayname
        if op not in ["<", "<=", "==", "!=", ">", ">="]:
            return

        # 获取左右表达式
        children = list(condition_expr.get_children())
        if len(children) < 2:
            return

        left_expr = children[0]
        right_expr = children[1]

        left_vars = self._extract_variable_names_from_expr(left_expr)
        right_vars = self._extract_variable_names_from_expr(right_expr)

        # 建立左右变量之间的关联
        for lv in left_vars:
            for rv in right_vars:
                lv_node = GlobalVarDict.get(lv)
                rv_node = GlobalVarDict.get(rv)
                if lv_node and rv_node:
                    if (lv_node, rv_node) not in subgraph:
                        subgraph.add((lv_node, rv_node))
                    lv_node.related_with_var.add(rv)
                    rv_node.related_with_var.add(lv)

    def prase_expression(self, node: Cursor) -> list[VarNode]:
        """
        解析一个非布尔表达式, 且不是一个赋值表达式
        并递归处理里面的子节点
        """
        res = []
        for child in node.get_children():
            if is_callVarNode(node):
                # 检查child 是否为一个变量引用, 如果是变量引用, 则将这个变量加入到 res
                var_name = node.spelling
                if is_callLocalVarNode(node):
                    var_node = self.LocalVarDict.get(var_name)
                elif is_callGlobalVarNode(node):
                    var_node = GlobalVarDict.get(var_name)
                elif is_callFuncParam(node):
                    raise NotImplementedError
                    # TODO
                    var_node = None
                else:
                    continue

                res.append(var_node)

            elif is_callFuncNode(child):
                # 检查child 是否为一个函数调用, 如果是, 则将这个函数的返回值对应的节点加入到res, 同时解析这个函数节点
                self.process_callFuncNode(child)
                func_name = node.spelling
                if FuncDict.get(func_name):
                    # 此处一般都会有函数信息节点, 因为函数声明必然在前面被解析过了
                    called_func_node = FuncDict.get(func_name)
                    if called_func_node.return_type != "void":
                        res.append(FuncDict.get(func_name).return_var)

                else:
                    logger.error("找不到调用函数的函数声明节点{node.spelling}")
            else:
                # TODO 其他情况递归解析
                for child in node.get_children():
                    self.prase_expression(child)

        raise NotImplementedError
        return res

    def prase_bool_expression(self, node: Cursor):
        """
        解析一个布尔表达式, 解析范围:
            for / while 循环的条件表达式

        """

        # 检查它是一个合法的bool表达式
        if node.kind != CursorKind.BINARY_OPERATOR:
            return
        if not node.spelling:
            return
        if len(list(node.get_children())) < 2:
            return

        tmp_set = {"==", ">", "<"}  # 不检查 非运算
        for op_str in tmp_set:
            if op_str in tmp_set:
                break
        else:
            return

        # 获取符号两边的表达式
        left_expr = node.get_children()[0]
        right_expr = node.get_children()[1]

        infor_node_l: list = self.prase_expression(left_expr)
        info_node_r: list = self.prase_expression(right_expr)

        return (infor_node_l, info_node_r)

    def process_returnStatement(self, node: Cursor):
        """
        解析返回语句

        node: return 语句
        处理C语言的返回语句, 对于void类型, 则直接跳过. 否则需要将返回的表达式所含有变量, 和ReturnVarNode做关联
        """

        if FuncDict.get(self.func_name) is None:
            return
        if FuncDict[self.func_name].return_type == "void":
            return

        if not list(node.get_children()):
            return
        # 处理其他的情况
        # 检查返回值是否为字面量/枚举常量等, 如果是则直接返回
        # 检查返回值是否为含变量的表达式, 解析那个表达式中含有的变量节点, 构建subgraph关系
        expr = node.get_children()[-1]
        return_node = FuncDict[self.func_name].return_var
        node_list = self.prase_expression(expr)

        for node in node_list:
            # 添加边关系
            self.subgraph.add((return_node, node))
        pass


class FuncAnalyzer2:
    def __init__(self, cursor: Cursor):
        self.cursor = cursor
        self.func_name = self.cursor.spelling
        if FuncDict[self.func_name].def_location != None:
            logger.warning(f"函数定义已经解析{self.func_name}")
            return

        self.func = FuncNode()  # TODO  需要贴到FuncDict中
        self.subgraph = set()
        self.level = 0

    def parse_funcDef(self):
        PreorderAST(
            self.cursor,
            lambda node: self.process_subNode(node),
            lambda node: node.get_children(),
        )

    def process_subScope(self, node: Cursor):
        cur_level = self.level + 1
        scope_node = node.get_children()[0]  # 作用域开始的节点
        for child in scope_node.get_children():
            self.process_subNode(child)

    def process_subNode(self, node: Cursor) -> bool:
        kind: CursorKind = node.kind
        if kind.is_expression():
            self.process_expr(node)
            return False
        elif kind.is_statement():
            self.process_stmt(node)
            return False
        elif kind.is_reference():
            self.process_ref(node)
            return False
        elif kind.is_declaration():
            self.process_decl(node)
            return False
        elif kind == CursorKind.COMPOUND_STMT:
            self.process_subScope(node)

        return True

    # %% 处理表达式
    def process_ref(self, node: Cursor):
        """
        if isGlobalVar:

        elif isLocalVar:

        """

    def process_decl(self, node: Cursor):
        """
        记录局部变量的声明信息, 若有赋值, 则进行赋值表达式解析
        """

        # TODO
        decl_var: LocalVarNode = None
        init_expr: Cursor = None
        init_var: list[VarNode] = self.process_expr(init_expr)
        for var in init_var:
            self.subgraph.add((decl_var, var))

    def process_expr(self, node: Cursor) -> list[VarNode]:
        """
        处理非边界解析表达式, 并将计算该表达式所直接使用的变量返回, 对于下一层的则不应该返回
        """
        res: list[VarNode] = []
        for child in node.get_children():
            # 如果是变量引用, 将变量加到res
            # 如果是函数调用, 则将函数ReturnVal加到res
            # 如果是子表达式, 则进入下层, 并将下层的返回值加到当前
            pass

        return res

    def process_varRef(self, node: Cursor):
        if node.kind == CursorKind.MEMBER_REF_EXPR:
            # 成员引用
            raise NotImplementedError
        elif node.kind == CursorKind.DECL_REF_EXPR:
            # 直接的变量应用表达式
            raise NotImplementedError

    def process_funcCallExpr(self, node: Cursor) -> VarNode | None:
        """
        处理一个函数调用表达式, 返回值为函数返回值对应的 VarNode, 若
        """

    def process_boundExpr(self, node: Cursor, core_var_str: str = None):
        """
        处理边界表达式 在 for while 中的 == != >= <= > < 等
        core_var_str是
        """
        # 解析两边
        if len(list(node.get_children())) != 2:
            logger.error(f"错误的边界表达式{node.spelling}")

        left_vars = self.process_expr(node.get_children()[0])
        right_vars = self.process_expr(node.get_children()[1])

        if len(left_vars + right_vars) < 2:
            logger.info("变量数量小于2, 无需构建边关联")
            return

        # 寻找核心变量
        core_var = (left_vars + right_vars)[0]
        if not core_var_str:
            for expr in left_vars + right_vars:
                if expr.spelling == core_var_str:
                    core_var = expr
                    break

        # 构建边关系
        for var in left_vars + right_vars:
            if var != core_var:
                self.subgraph.add((core_var, var))

    def process_assignExpr(self, node: Cursor) -> VarNode | None:
        """
        处理赋值表达式, 形如 *x = y + i
        赋值表达式的值为左侧的变量
        """
        if len(node.get_children()) != 2:
            logger.error(f"错误的赋值表达式{node.spelling}")
            return None

        left_expr = node.get_children()[0]
        right_expr = node.get_children()[1]
        rvar_list = self.process_expr(right_expr)

        # 对于  *x = y + i, *x将被解析为x的指向物
        # TODO 对于复杂的情况呢?  *(arr+f(x)) = y + i; 寄希望于 self.process_expr 可以返回arr的元素Node?
        left_vars = self.process_expr(left_expr)
        if len(left_vars) != 1:
            logger.error(f"错误的赋值表达式{node.spelling}")
            return None

        left_var = left_vars[0]
        for rvar in rvar_list:
            self.subgraph.add((left_var, rvar))

        return left_var

    # %% 处理statement
    def process_stmt(self, node: Cursor):
        """
        解析语句, 先判断是否是一个return语句
        """
        # if return statment, process_expr
        if is_returnStatement(node):
            self.process_returnStmt(node)
        elif is_whileLoopNode(node):
            self.process_whileStmt(node)
        elif is_forLoopNode(node):
            self.process_forStmt(node)
        elif is_ifStmt(node):
            self.process_ifStmt(node)
        elif is_switchStmt(node):
            self.process_swithstmt(node)

    def process_switchStmt(self, node: Cursor):
        children = list(node.get_children())
        if len(children) < 2:
            logger.debug("错误的 switch 块")
            return

        switch_obj = children[0]
        switch_body = children[1]
        self.process_expr(switch_obj)

        for child in switch_body.get_children():
            if child.kind in {CursorKind.CASE_STMT, CursorKind.DEFAULT_STMT}:
                self.process_caseStmt(child)
            elif child.kind == CursorKind.BREAK_STMT:
                continue
            else:
                continue

    def process_ifStmt(self, node: Cursor):
        for child in node.get_children():
            self.process_subNode(child)

    def process_returnStmt(self, node: Cursor):
        if len(node.get_children()) == 0:
            return
        if self.func.return_type == "void":
            return

        var_list = self.process_expr(node.get_children()[0])

        for var in var_list:
            self.subgraph.add((self.func.return_var, var))

    def process_forStmt(self, node: Cursor):
        """
        处理for语句的 for() 的内容
        TODO 问题在于遇到不规范的for循环表达式, 它的语法树结构无法判断是哪一个, 比如for(int i = 1; i < 10;;) ++i; 此时只有三个子节点
        """
        for_sub_stat = list(node.get_children())
        if len(for_sub_stat) != 4:
            logger.debug("不是规范的for循环体")

        # if len(for_sub_stat) < 3:
        #     logger.error(f"错误的for语句{node.spelling}")

        init_stat, boundary_stat, increase_stat, for_body = for_sub_stat

        if boundary_stat.kind == CursorKind.NULL_STMT:
            # 没有边界, 则应该直接返回 TODO 可能还要解析下子作用域
            return

        # 提取核心变量
        core_var_str: str | None = None
        if init_stat.kind != CursorKind.NULL_STMT:
            # TODO提取核心变量
            pass

        if not core_var_str and increase_stat != CursorKind.NULL_STMT:
            # TODO提取核心变量
            pass

        self.process_boundExpr(boundary_stat.get_children(), core_var_str)

        # 处理 for 循环体

    def process_whileStmt(self, node: Cursor):
        """
        处理while语句的 while()的内容
        """
        while_children: list[Cursor] = list(node.get_children())
        if len(while_children) != 2:
            logger.error(f"错误的while语句和边界表达式{node.spelling}")
            return

        boundary_expr, while_body = while_children
        self.process_boundExpr(boundary_expr)

        if while_body.kind == CursorKind.COMPOUND_STMT:
            self.process_subScope(while_body)
        else:
            self.process_expr(while_body)
