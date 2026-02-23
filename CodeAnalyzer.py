from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import clang.cindex
from clang.cindex import Cursor, CursorKind, StorageClass, TypeKind
from scripts.h2py import process


# %% 数据结构定义
@dataclass(frozen=True)
class CodeLocation:
    """
    记录代码元素在文件中的位置
    """

    file: Path  # 所属文件路径
    begin: int = None  # 起始行号（None表示未指定）
    end: int = None  # 结束行号（None表示未指定）


@dataclass
class Parameter:
    """
    函数参数信息类
    记录单个函数参数的相关属性
    """

    # 参数名称
    name: str
    position: int      # 参数在函数参数列表中的位置, 从0开始计算
    type_name: str     # 参数类型名称（如int、char*等）


@dataclass
class FuncNode:
    """
    函数节点信息类
    存储函数的完整信息，包括声明/定义位置、参数、调用关系等
    """
    name: str     # 函数名称
    parameters: list[Parameter] = field(default_factory=[])  # 函数参数列表
    return_type: str = None
    decl_location: CodeLocation = None  # 函数声明的代码位置
    def_location: CodeLocation = None  # 函数定义的代码位置
    # 该函数调用的其他函数名称列表
    called_func: set[str] = field(default_factory=set())
    # 该函数调用的变量名称列表
    called_var: set[str] = field(default_factory=set())


@dataclass
class GlobalVarNode:
    """
    全局变量节点信息类
    存储全局变量（包括静态全局变量）的完整信息
    """

    # 变量名称
    name: str            # 变量完整类型名称（如int、char[]、int*等）
    type_name: str       # 变量纯类型名称（去除数组/指针修饰，如char[]的纯类型为char）
    pure_type_name: str  # 纯类型名/原始类型名，去除数组、指针等修饰
    is_array: bool = False       # 是否为数组类型
    is_pointer: bool = False     # 是否为指针类型
    is_static: bool = False      # 是否为静态变量（static）
    decl_location: CodeLocation = None     # 变量声明位置
    def_location: CodeLocation = None     # 变量定义位置

    @classmethod
    def from_cursor(cls, node: Cursor):
        """
        从clang的Cursor对象创建GlobalVarNode实例
        :param node: clang解析得到的游标对象
        :return: GlobalVarNode实例
        """
        return CodeAnalyzer.process_GlobalVarNode(node)


# 全局变量字典：键为变量名，值为GlobalVarNode对象
GlobalVarDict: defaultdict[str, GlobalVarNode] = defaultdict()
# 函数字典：键为函数名，值为FuncNode对象
FuncDict: defaultdict[str, FuncNode] = defaultdict()


class CodeAnalyzer:
    """
    C代码分析器核心类
    基于clang解析C代码，提取函数、全局变量等信息，分析调用关系
    """

    def __init__(self, core_macro_inc: Callable[[Path], tuple]):
        """
        初始化代码分析器
        :param core_macro_inc: 回调函数，输入文件路径，返回元组(coreKind, macro_commands, respFile)
                               core: 核类型标识
                               macro_commands: 宏定义命令列表（如-DXXX=1）
                               respFile: 响应文件路径（包含头文件包含路径等）
        """
        self.core_macro_inc = core_macro_inc

        # TODO 初始化其他需要的属性/变量

    def _respF2List(self, resp_file: Path) -> list[str]:
        """
        读取响应文件（respFile）内容并转换为列表
        响应文件通常包含头文件搜索路径等编译参数
        :param resp_file: 响应文件路径
        :return: 响应文件中的每行内容组成的列表
        """
        with open(resp_file) as f:
            return f.readlines()

    def parse_file(self, file_path: Path):
        """
        解析指定的C文件，提取函数、变量等信息
        :param file_path: 要解析的C文件路径
        """
        # 记录当前正在解析的文件（绝对路径）
        self.cur_parse_file = file_path.resolve()

        # 获取核心类型、宏命令、响应文件路径
        coreKind, macro_commands, respFile = self.core_macro_inc(file_path)
        # 读取响应文件中的包含路径等命令
        inc_commands = self._respF2List(respFile)

        # 构建clang解析参数
        arg_command = ["-x", "c", "-std=c99"]  # 指定解析语言为C，遵循C99标准
        if inc_commands:
            arg_command.extend(inc_commands)  # 添加头文件包含路径
        if macro_commands:
            arg_command.extend(macro_commands)  # 添加宏定义

        # 创建clang索引并解析文件，获取根游标
        root = clang.cindex.Index.create().parse(file_path, args=arg_command).cursor

        # 遍历根游标下的子节点
        for child in root.get_children():
            self.process_Node(child)

    def isFuncDef(self, node: Cursor) -> bool:
        """
        判断游标节点是否为函数定义, 必须是函数声明类型，且必须包含函数体（is_definition）
        """
        return node.kind == CursorKind.FUNCTION_DECL and node.is_definition()

    def process_Node(self, node: Cursor):
        """
        处理单个游标节点, 仅处理当前解析文件内的节点（排除头文件等外部文件）
        :param node: clang游标节点
        """
        # 检查节点所属文件是否为当前解析文件
        if (
            node.location.file.name
            and Path(node.location.file.name).resolve() == self.cur_parse_file
        ):
            # 变量声明节点（全局变量/静态变量）
            if node.kind == CursorKind.VAR_DECL:
                self.process_GlobalVarNode(node)
            # 函数声明节点
            elif node.kind == CursorKind.FUNCTION_DECL:
                self.process_funcDeclNode(node) # 记录函数声明信息
                if not node.is_definition():
                    return

                # 处理函数定义信息
                subgraph = dict()
                self.process_funcDefNode(node, subgraph)

                # 将子图信息更新到全局图中
                # TODO

    @staticmethod
    def process_GlobalVarNode(node: Cursor):
        """
        处理全局变量游标节点，提取全局变量信息
        （未实现：需补充完整的变量类型解析逻辑）
        :param node: 全局变量对应的游标节点
        """

        var_name = node.spelling
        if var_name in GlobalVarDict and GlobalVarDict[var_name].def_location is not None:
            # 已经处理过
            return

        if node.storage_class is None: # 判断存储类型（static/extern等）
            return

        type_name = node.type.spelling
        is_pointer = node.type.kind == TypeKind.POINTER
        is_array = node.type.kind in (TypeKind.CONSTANTARRAY, TypeKind.INCOMPLETEARRAY)
        is_static = node.storage_class == StorageClass.STATIC
        loc = CodeLocation(Path(node.location.file.name).resolve(), node.location.start.line, node.extent.end.line)

        var_node = GlobalVarNode(
            name=var_name,
            type_name=type_name,
            pure_type_name=type_name, # TODO
            is_array=is_array,
            is_pointer=is_pointer,
            is_static=is_static,
            decl_location=loc,
            def_location=loc if node.is_definition() else None
        )

        GlobalVarDict[var_name] = var_node
        # raise NotImplementedError

    def process_funcDeclNode(self, node: Cursor):
        """
        处理函数声明节点, 获取基本信息
        """
        # 1. 获取函数名称
        func_name = node.spelling
        if not func_name:
            return

        if func_name in FuncDict:
            # 已有声明信息
            return

        # 2. 获取返回类型
        return_type = node.result_type.spelling

        # 3. 提取参数列表
        parameters: list[Parameter] = []
        for i, arg in enumerate(node.get_arguments()):
            # 处理可能的匿名参数 (如 void func(int);)
            p_name = arg.spelling if arg.spelling else f"arg{i}"
            parameters.append(Parameter(
                name=p_name,
                position=i,
                type_name=arg.type.spelling
            ))

        # 4. 获取位置信息
        # 使用 node.extent 获取包含返回类型到结尾的完整代码区间
        decl_location = CodeLocation(
            file=Path(node.location.file.name).resolve(),
            begin=node.extent.start.line,
            end=node.extent.end.line
        )

        # 5. 创建并存储 FuncNode
        new_func = FuncNode(
            name=func_name,
            parameters=parameters,
            return_type=return_type,
            def_location = decl_location
        )

        FuncDict[func_name] = new_func


    def process_funcDefNode(self, node: Cursor, subgraph:dict):
        """
        处理函数定义节点，深度遍历函数内所有子节点
        :param node: 函数定义对应的游标节点
        :param subgraph: 子图字典，用于记录函数内的节点关系
        """
        # 前序遍历函数节点的所有子节点（包括嵌套节点）
        pre_node = None
        for n in node.walk_preorder():
            self.process_subNodeInFunc(n, pre_node)
            pre_node = n

    def process_subNodeInFunc(self, cur_node: Cursor, pre_node: Cursor = None):
        """
        处理函数内的子节点，分析函数调用、数组索引、枚举调用等
        函数内部节点的核心处理入口
        :param cur_node: 当前遍历的游标节点
        :param pre_node: 上一个遍历的游标节点（默认None）
        """
        # # 处理函数调用节点
        # self.process_callFuncNode(cur_node)
        # # 处理数组索引节点
        # self.process_arrIdxNode(pre_node, cur_node)
        # # 处理枚举调用节点
        # self.process_callEnumNode(cur_node)
        # # 处理局部变量调用节点
        # self.process_callLocalVarNode(cur_node)
        # # 处理全局变量调用节点
        # self.process_callGlobalVarNode(cur_node)
        # # 处理变量赋值节点
        # self.process_varAssignNode(cur_node)

        self.process_callLocalVarNode(cur_node)
        self.process_callGlobalVarNode(cur_node)
        self.process_localVarDecalNode(cur_node)
        self.process_varAssignNode(cur_node)
        self.process_callFuncNode(cur_node)
        self.process_callEnumNode(cur_node)
        self.process_arrIdxNode(cur_node, pre_node)

    def process_callLocalVarNode(self, node: Cursor):
        """
        处理局部变量调用节点
        提取函数内调用的局部变量信息（未实现具体逻辑）
        :param node: 局部变量调用对应的游标节点
        """
        # 待实现：解析函数内调用的局部变量
        pass

    def process_callGlobalVarNode(self, node: Cursor):
        """
        处理全局变量调用节点
        提取函数内调用的全局变量信息（未实现具体逻辑）
        :param node: 全局变量调用对应的游标节点
        """
        # 待实现：解析函数内调用的全局变量，更新FuncNode的called_var
        pass

    def process_localVarDecalNode(self, node: Cursor):
        """
        处理局部变量声明节点
        提取函数内定义的局部变量信息（未实现具体逻辑）
        :param node: 局部变量声明对应的游标节点
        """
        # 记录局部变量的声明
        pass

    def process_varAssignNode(self, node: Cursor):
        """
        处理变量赋值节点
        解析代码中的变量赋值操作，提取赋值关系（如变量被赋予的值、赋值位置等）
        :param node: 赋值操作对应的clang游标节点
        """
        # 待实现：解析赋值表达式，记录变量赋值的目标变量、赋值来源（常量/变量/表达式）等信息
        # 如果左右两边都是含有变量的表达式, 则将他们关联起来
        pass

    def process_callFuncNode(self, node: Cursor):
        """
        处理函数调用节点
        识别代码中调用的函数，记录调用关系（如当前函数调用了哪些其他函数）
        :param node: 函数调用对应的clang游标节点
        """
        # 待实现：提取被调用函数的名称，更新对应FuncNode的called_func列表
        # 检查传入的实参, 如果是全局变量, 则应该有关联关系
        pass

    # def process_callVarNode(self, node: Cursor):
    #     """
    #     处理变量调用节点
    #     识别代码中访问的变量（全局/局部），记录变量使用关系
    #     :param node: 变量访问对应的clang游标节点
    #     """
    #     # 待实现：提取被访问变量的名称，区分全局/局部变量，更新对应FuncNode的called_var列表
    #     pass

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

    def process_arrIdxNode(self, cur_node: Cursor, pre_node: Cursor):
        """
        处理数组索引访问节点
        解析数组下标访问操作，分析索引的构成（常量/表达式）
        :param cur_node: 当前遍历到的游标节点（通常是数组索引节点）
        :param pre_node: 上一个遍历的游标节点（通常是数组变量节点）
        """
        # 检查数组下标是字面量, 还是表达式, 并解析表达式中含有的变量, 这些变量将和数组关联
        pass


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


def SymbolStructure(proj_dir: Path, compile_config: str):
    """
    项目级C代码符号分析入口函数
    解析指定项目下所有C文件的符号结构（函数、全局变量、调用关系等）
    :param proj_dir: 项目根目录路径（Path对象），分析该目录下的所有C文件
    :param compile_config: 编译配置字符串（如编译选项、宏定义等），用于适配不同编译环境
    :return: 包含分析器所有字段的字典，存储解析得到的所有符号信息
    """
    from MyPyLib.Preprocessor import Preprocessor

    # 定义响应文件输出目录（存储预处理后的头文件路径、宏定义等）
    resp_dir = Path("./resp/")
    # 初始化预处理器，处理项目的编译配置和文件依赖
    pre_er = Preprocessor(proj_dir, resp_dir, compile_config=compile_config)

    # 初始化代码分析器，传入预处理器的宏/头文件处理回调函数
    code_analyzer = CodeAnalyzer(pre_er.core_macro_inc)
    # 获取项目中所有被使用的文件（包括C文件、头文件等）
    all_used_files: set[Path] = pre_er.getUsedFiles()

    # TODO 过滤出需要解析的C源文件（排除头文件等）
    all_used_cfiles: list[Path] = [
        f for f in all_used_files if f.suffix.lower() == ".c"
    ]

    # 遍历所有C文件，逐个解析符号信息
    for c_file in all_used_cfiles:
        code_analyzer.parse_file(c_file)

    # 返回分析器对象的所有字段（包含解析得到的FuncDict、GlobalVarDict等）
    return get_object_fields(code_analyzer)
