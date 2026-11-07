/* SPDX-License-Identifier: GPL-3.0-or-later
 * Rebind existing ELF imports of clock_gettime. No inline patches or vDSO changes.
 */
#define _GNU_SOURCE
#include <elf.h>
#include <link.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#if __SIZEOF_POINTER__ == 8 && defined(__x86_64__)
struct patch_context { void *replacement; int count, failed; };
static int protection(uintptr_t address) {
    FILE *maps=fopen("/proc/self/maps","r");
    if (!maps) return -1;
    char line[4096], mode[5]; unsigned long start,end;
    int result=-1;
    while (fgets(line,sizeof(line),maps)) {
        if (sscanf(line,"%lx-%lx %4s",&start,&end,mode)==3 && address>=start && address<end) {
            result=(mode[0]=='r'?PROT_READ:0)|(mode[1]=='w'?PROT_WRITE:0)|(mode[2]=='x'?PROT_EXEC:0);
            break;
        }
    }
    fclose(maps); return result;
}
static void relocate(struct dl_phdr_info *info, ElfW(Rela) *entries, size_t bytes,
                     ElfW(Sym) *symbols, const char *strings, struct patch_context *context) {
    long length=sysconf(_SC_PAGESIZE);
    if (length<=0) { context->failed=1; return; }
    for (size_t i=0;i<bytes/sizeof(*entries);i++) {
        unsigned type=ELF64_R_TYPE(entries[i].r_info);
        if (type!=R_X86_64_JUMP_SLOT && type!=R_X86_64_GLOB_DAT) continue;
        if (strcmp(strings+symbols[ELF64_R_SYM(entries[i].r_info)].st_name,"clock_gettime")) continue;
        uintptr_t *slot=(void *)(info->dlpi_addr+entries[i].r_offset);
        if (__atomic_load_n(slot,__ATOMIC_ACQUIRE)==(uintptr_t)context->replacement) continue;
        uintptr_t page=(uintptr_t)slot & ~((uintptr_t)length-1);
        int previous=protection((uintptr_t)slot);
        if (previous<0 || (!(previous&PROT_WRITE) && mprotect((void *)page,length,previous|PROT_WRITE))) {
            context->failed=1; continue;
        }
        __atomic_store_n(slot,(uintptr_t)context->replacement,__ATOMIC_RELEASE);
        context->count++;
        if (!(previous&PROT_WRITE) && mprotect((void *)page,length,previous)) context->failed=1;
    }
}
static int module(struct dl_phdr_info *info, size_t size, void *data) {
    (void)size;
    ElfW(Dyn) *dynamic=NULL; ElfW(Sym) *symbols=NULL;
    const char *strings=NULL; ElfW(Rela) *plt=NULL,*rela=NULL;
    size_t plt_size=0,rela_size=0; long plt_type=DT_RELA;
    for (int i=0;i<info->dlpi_phnum;i++)
        if (info->dlpi_phdr[i].p_type==PT_DYNAMIC)
            dynamic=(void *)(info->dlpi_addr+info->dlpi_phdr[i].p_vaddr);
    if (!dynamic) return 0;
    for (;dynamic->d_tag;dynamic++) {
        uintptr_t address=dynamic->d_un.d_ptr;
        if (address<info->dlpi_addr) address+=info->dlpi_addr;
        switch (dynamic->d_tag) {
            case DT_SYMTAB: symbols=(void *)address; break;
            case DT_STRTAB: strings=(void *)address; break;
            case DT_JMPREL: plt=(void *)address; break;
            case DT_PLTRELSZ: plt_size=dynamic->d_un.d_val; break;
            case DT_PLTREL: plt_type=dynamic->d_un.d_val; break;
            case DT_RELA: rela=(void *)address; break;
            case DT_RELASZ: rela_size=dynamic->d_un.d_val; break;
        }
    }
    if (!symbols || !strings) return 0;
    if (plt && plt_type==DT_RELA) relocate(info,plt,plt_size,symbols,strings,data);
    if (rela) relocate(info,rela,rela_size,symbols,strings,data);
    return 0;
}
int ge_speed_rebind(void *replacement) {
    struct patch_context context={replacement,0,0};
    dl_iterate_phdr(module,&context);
    return context.failed ? -1 : context.count;
}
#else
int ge_speed_rebind(void *replacement) { (void)replacement; return -2; }
#endif
