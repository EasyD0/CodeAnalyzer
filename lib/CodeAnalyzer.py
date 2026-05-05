from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from clang.cindex import Cursor, CursorKind, StorageClass, TypeKind, Type, Index
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
    is_localVarDeclNode,  #
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

logger = logSetup("CodeA")


class CodeAnalyzer:
    """
    C代码分析器核心类
    基于clang解析C代码，提取函数、全局变量等信息，分析调用关系
    """

    def __init__(self, core_macro_inc: Callable[[Path], tuple]):
        """
        初始化代码分析器
        :param core_macro_inc: 回调函数，该函数接受一个文件路径，返回元组(coreKind, macro_commands, respFile)
                               core: 核类型标识
                               macro_commands: 宏定义命令列表（如-DXXX=1）
                               respFile: 响应文件路径（包含头文件包含路径等）
        """
        self.core_macro_inc = core_macro_inc

        # TODO 初始化其他需要的属性/变量

    def _respF2List(self, resp_file: Path) -> list[str]:
        # 将resp文件内容转为list
        with open(resp_file) as f:
            return f.readlines()

    def parse_file(self, file_path: Path):
        """
        解析一个文件
        """
        # 暂存下正在解析的当前文件路径
        self.cur_parse_file = file_path.resolve()

        coreKind, macro_commands, respFile = self.core_macro_inc(file_path)
        inc_commands = self._respF2List(respFile)

        arg_command = ["-x", "c", "-std=c99"]
        if inc_commands:
            arg_command.extend(inc_commands)
        if macro_commands:
            arg_command.extend(macro_commands)

        root = Index.create().parse(file_path, args=arg_command).cursor

        for child in root.get_children():
            # 处理每个全局节点
            self.process_globalNode(child)

    def process_globalNode(self, node: Cursor):
        """
        处理单个全局节点, 仅处理当前解析文件内的节点
        """
        if not (
            node.location.file.name
            and Path(node.location.file.name).resolve() == self.cur_parse_file
        ):
            # 仅检查当前翻译单元, 在当前文件中的节点
            logger.warning(
                f"发现当前翻译单元中的无效节点{self.cur_parse_file} : {node.spelling}"
            )
            return

        if is_globalVarDeclNode(node):
            self.process_globalVarNode(node)

        elif is_funcDecl(node):
            self.process_funcDeclNode(node)  # 记录函数声明信息

            if not is_funcDef(node):
                return

            self.process_funcDefNode(node)

            # TODO 将子图信息更新到全局图中

    @staticmethod
    def process_globalVarNode(node: Cursor) -> GlobalVarNode | None:
        """
        处理全局变量仅声明/定义节点
        """
        var_name = node.spelling
        if (
            var_name in GlobalVarDict
            and GlobalVarDict[var_name].def_location is not None
        ):
            # 已经找到过定义了
            return GlobalVarDict[var_name]

        if node.storage_class is None:  # 判断存储类型（static/extern等）
            # TODO 这里需要验证/修改
            return

        _type_name = typeName(node)
        _pure_type_name = pureTypeName(node)
        _is_pointer = is_pointer(node)
        _is_array = is_array(node)
        _is_static = is_static(node)
        _is_struct = is_struct(node)
        _is_union = is_union(node)

        code_loc = CodeLocation.from_cursor(node)

        global_var = GlobalVarNode(
            name=var_name,
            type_name=_type_name,
            pure_type_name=_pure_type_name,
            var_category=TypeCategory.from_cursor(node),
            is_static=_is_static,
            decl_location=code_loc,
            def_location=code_loc if node.is_definition() else None,
        )

        GlobalVarDict[var_name] = global_var
        memberListParseRecursive(global_var, node.type)
        return global_var

    @staticmethod
    def process_funcDeclNode(node: Cursor) -> FuncNode | None:
        """
        处理函数声明节点, 获取基本信息
        """
        func_name = node.spelling
        if not func_name:
            return None

        if func_name in FuncDict:
            return FuncDict[func_name]

        return_type_name = node.result_type.get_canonical().spelling

        # 处理形参列表
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

        # 获取代码位置
        decl_location = CodeLocation.from_cursor(node)

        new_func = FuncNode(
            name=func_name,
            parameters=parameters,
            return_type=return_type_name,
            def_location=decl_location,
        )

        FuncDict[func_name] = new_func

        return new_func

    def process_funcDefNode(
        self,
        root_node: Cursor,
    ):
        """
        处理函数定义节点，深度遍历函数内所有子节点
        :param root_node: 函数定义对应的游标节点
        :param subgraph: 子图，用于记录函数内的节点关系
        """
        func_name = root_node.spelling
        # 局部变量字典, 记录局部变量的基本信息, 用于构建subgraph
        self.LocalVarDict: defaultdict[str, LocalVarNode] = defaultdict()
        self.cur_parse_funcname = func_name
        # 一个函数内 所引用东西关联图, 每个元素是一个有向边, 一个有向边为tuple[src, dst]
        self.subgraph: set[tuple[Any, Any]] = (
            set()  # TODO 这就必须要求每个节点是不可变的或者可哈希的? 这有些麻烦了
        )

        if not func_name:
            logger.error("函数名不存在")
            return

        if func_name not in FuncDict:
            self.process_funcDeclNode(root_node)

        if FuncDict[func_name].def_location is not None:
            logger.warning(
                f"函数{func_name}在 {FuncDict[func_name].def_location} 已有定义"
            )
            return

        # 更新形参名称
        for i, p in enumerate(root_node.get_arguments()):
            p_type_name = typeName(p.type.get_canonical())
            if p_type_name == "void":  # 处理形参列表为(void)的情况
                break

            p_name = p.spelling if p.spelling else f"parameter{i}"

            # TODO 需要防止代码中出现声明和定义不一致的情况
            FuncDict[func_name].parameters[i].name = p_name

        # 更新def_location
        FuncDict[func_name].def_location = CodeLocation.from_cursor(root_node)

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
            root_node,
            lambda node: self.process_subNodeInFunc(node),
            lambda node: node.get_children(),
        )

    def process_subNodeInFunc(
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
        func_node = FuncDict.get(self.cur_parse_funcname)

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
            in_func=self.cur_parse_funcname,
        )

        self.LocalVarDict[var_name] = local_var
        memberListParseRecursive(local_var, node.type)

        # 增加图连接关系
        self.subgraph.add((FuncDict[self.cur_parse_funcname], local_var))

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

        cur_func_node = FuncDict[self.cur_parse_funcname]
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

            # arg_vars = self._extract_variable_names_from_expr(arg
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

        if FuncDict.get(self.cur_parse_funcname) is None:
            return
        if FuncDict[self.cur_parse_funcname].return_type == "void":
            return

        if not list(node.get_children()):
            return
        # 处理其他的情况
        # 检查返回值是否为字面量/枚举常量等, 如果是则直接返回
        # 检查返回值是否为含变量的表达式, 解析那个表达式中含有的变量节点, 构建subgraph关系
        expr = node.get_children()[-1]
        return_node = FuncDict[self.cur_parse_funcname].return_var
        node_list = self.prase_expression(expr)

        for node in node_list:
            # 添加边关系
            self.subgraph.add((return_node, node))
        pass


# %% 其他函数
def get_object_fields(obj):
    """
    获取对象的所有字段（属性）及其值
    兼容普通属性和@property装饰器定义的属性，排除私有属性和方法
    parameters:
        obj: 任意Python对象（如实例化的CodeAnalyzer、FuncNode等）
    returns: dict: {属性名: 属性值} 键值对形式返回对象的所有公开属性
    """
    # 1 先通过vars()获取对象的基础属性字典，并复制避免修改原对象
    result = vars(obj).copy()  # 初始结果包含对象__dict__中的属性

    # 2 补充获取__dict__中没有的@property装饰器定义的属性
    #   遍历对象的所有属性名，过滤需要补充的属性
    for attr in dir(obj):
        # 过滤条件：非私有属性（不以_开头）、非可调用对象（排除方法/函数）
        if not attr.startswith("_") and not callable(getattr(obj, attr)):
            # 若该属性未在初始结果中，则尝试获取并添加
            if attr not in result:
                try:
                    result[attr] = getattr(obj, attr)
                except Exception:
                    pass  # 捕获获取属性时的异常（如属性访问权限问题），跳过该属性

    return result


# def SymbolStructure(proj_dir: Path, compile_config: str):
#     """
#     项目级C代码符号分析入口函数
#     解析指定项目下所有C文件的符号结构（函数、全局变量、调用关系等）
#     :param proj_dir: 项目根目录路径（Path对象），分析该目录下的所有C文件
#     :param compile_config: 编译配置字符串（如编译选项、宏定义等），用于适配不同编译环境
#     :return: 包含分析器所有字段的字典，存储解析得到的所有符号信息
#     """
#     from MyPyLib.Preprocessor import Preprocessor
#
#     # 定义响应文件输出目录（存储预处理后的头文件路径、宏定义等）
#     resp_dir = Path("./resp/")
#     # 初始化预处理器，处理项目的编译配置和文件依赖
#     pre_er = Preprocessor(proj_dir, resp_dir, compile_config=compile_config)
#
#     # 初始化代码分析器，传入预处理器的宏/头文件处理回调函数
#     code_analyzer = CodeAnalyzer(pre_er.core_macro_inc)
#     # 获取项目中所有被使用的文件（包括C文件、头文件等）
#     all_used_files: set[Path] = pre_er.getUsedFiles()
#
#     # TODO 过滤出需要解析的C源文件（排除头文件等）
#     all_used_cfiles: list[Path] = [
#         f for f in all_used_files if f.suffix.lower() == ".c"
#     ]
#
#     # 遍历所有C文件，逐个解析符号信息
#     for c_file in all_used_cfiles:
#         code_analyzer.parse_file(c_file)
#
#     # 返回分析器对象的所有字段（包含解析得到的FuncDict、GlobalVarDict等）
#     return get_object_fields(code_analyzer)

